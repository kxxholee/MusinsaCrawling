from __future__ import annotations

import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .categories import discover_category_tree
from .checkpoint import append_rows_csv
from .config import BRAND, DEFAULT_OUTPUT, GENDER_FILTER
from .detail import collect_detail_and_options
from .driver_pool import DriverPool
from .excel import save_excel
from .listing import collect_products_from_category
from .logger import log_error, log_info, log_ok, log_warn, make_progress, rule
from .schemas import OptionRow, ProductRow, SkuRow


def crawl_listing_unit(
    pool: "DriverPool",
    unit: Dict[str, str],
    max_products: Optional[int],
    max_scrolls: int,
    delay: float,
) -> List[ProductRow]:
    with pool.borrow() as driver:
        rows = collect_products_from_category(
            driver=driver,
            major_category=unit["major_category"],
            category_code=unit["category_code"],
            max_products=max_products,
            max_scrolls=max_scrolls,
            raw_category=unit["raw_category"],
        )

        time.sleep(delay)

        return rows


def crawl_detail_unit(
    pool: "DriverPool",
    product: ProductRow,
    delay: float,
) -> Tuple[ProductRow, List[OptionRow], List[SkuRow]]:
    with pool.borrow() as driver:
        result = collect_detail_and_options(driver=driver, product=product)

        time.sleep(delay)

        return result


def dedupe_products(products: List[ProductRow]) -> List[ProductRow]:
    """같은 상품 중복 제거. 대분류가 다르면 별도 행으로 유지."""
    result: List[ProductRow] = []
    seen = set()

    for product in products:
        key = (
            product.major_category,
            product.product_id or product.product_url,
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(product)

    return result


def make_checkpoint_dir(output_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    return output_path.with_name(f"{output_path.stem}_checkpoint_{timestamp}")


def main(
    output: Any = DEFAULT_OUTPUT,
    max_products: int = 10,
    max_scrolls: int = 80,
    delay: float = 0.8,
    headless: bool = False,
    skip_options: bool = False,
    workers: int = 4,
    browser: str = "auto",
) -> Path:
    max_products_resolved: Optional[int] = None if max_products == 0 else max_products

    output_path = Path(output)
    checkpoint_dir = make_checkpoint_dir(output_path)

    rule("무신사 스탠다드 여성 SKU 생성기 데모")

    log_info(f"브랜드: {BRAND}")
    log_info(f"여성 필터: gf={GENDER_FILTER} 고정")
    log_info("카테고리: 런타임에 동적 발견 (서브카테고리 단위 크롤)")
    log_info(
        f"카테고리별 최대 상품 수: "
        f"{'전체' if max_products_resolved is None else max_products_resolved}"
    )
    log_info(f"옵션 수집: {'건너뜀' if skip_options else '실행'}")
    log_info(f"브라우저: {browser}")
    log_info(f"저장 파일: {output_path}")
    log_info(f"중간 저장 폴더: {checkpoint_dir}")

    all_products: List[ProductRow] = []
    all_options: List[OptionRow] = []
    all_skus: List[SkuRow] = []

    workers_resolved = max(1, int(workers))

    log_info(f"병렬 워커: {workers_resolved}개")

    pool = DriverPool(size=workers_resolved, headless=headless, browser=browser)

    category_tree: List[Dict[str, Any]] = []

    try:
        with pool.borrow() as driver:
            category_tree = discover_category_tree(driver)

        if not category_tree:
            log_warn("카테고리 트리가 비어 있어 크롤을 중단합니다.")
            return output_path

        work_units: List[Dict[str, str]] = []

        for major in category_tree:
            for sub in major["subs"]:
                work_units.append(
                    {
                        "major_category": major["major_name"],
                        "category_code": sub["code"],
                        "raw_category": sub["name"],
                    }
                )

        append_rows_csv(
            checkpoint_dir / "category_master.csv",
            [
                {
                    "major_category": major["major_name"],
                    "category_code": sub["code"],
                    "raw_category": sub["name"],
                }
                for major in category_tree
                for sub in major["subs"]
            ],
        )

        with make_progress() as progress:
            listing_task = progress.add_task("서브카테고리 수집", total=len(work_units))

            seen_product_ids: set = set()

            with ThreadPoolExecutor(max_workers=workers_resolved) as executor:
                futures = {
                    executor.submit(
                        crawl_listing_unit,
                        pool,
                        unit,
                        max_products_resolved,
                        max_scrolls,
                        delay,
                    ): unit
                    for unit in work_units
                }

                for future in as_completed(futures):
                    unit = futures[future]

                    try:
                        rows = future.result()

                    except Exception as exc:
                        log_error(f"{unit['category_code']} 수집 실패: {exc}")

                        append_rows_csv(
                            checkpoint_dir / "listing_errors.csv",
                            [
                                {
                                    "major_category": unit.get("major_category", ""),
                                    "category_code": unit.get("category_code", ""),
                                    "raw_category": unit.get("raw_category", ""),
                                    "error": repr(exc),
                                }
                            ],
                        )

                        progress.advance(listing_task)
                        continue

                    new_rows: List[ProductRow] = []

                    for row in rows:
                        key = row.product_id or row.product_url

                        if not key or key in seen_product_ids:
                            continue

                        seen_product_ids.add(key)
                        all_products.append(row)
                        new_rows.append(row)

                    append_rows_csv(checkpoint_dir / "products_listing.csv", new_rows)

                    progress.advance(listing_task)

            log_ok(f"[목록 수집 완료] 중복 제거 후 상품 수: {len(all_products)}")

            if not skip_options and all_products:
                detail_task = progress.add_task(
                    "상세 페이지/옵션 수집",
                    total=len(all_products),
                )

                updated_by_id: Dict[str, ProductRow] = {}

                with ThreadPoolExecutor(max_workers=workers_resolved) as executor:
                    futures = {
                        executor.submit(crawl_detail_unit, pool, product, delay): product
                        for product in all_products
                    }

                    for future in as_completed(futures):
                        src = futures[future]

                        try:
                            updated_product, option_rows, sku_rows = future.result()

                        except Exception as exc:
                            log_error(f"{src.product_id} 상세 실패: {exc}")

                            updated_by_id[src.product_id or src.product_url] = src

                            append_rows_csv(
                                checkpoint_dir / "detail_errors.csv",
                                [
                                    {
                                        "product_id": src.product_id,
                                        "product_name": src.product_name,
                                        "product_url": src.product_url,
                                        "major_category": src.major_category,
                                        "raw_category": src.raw_category,
                                        "error": repr(exc),
                                    }
                                ],
                            )

                            progress.advance(detail_task)
                            continue

                        updated_by_id[
                            updated_product.product_id or updated_product.product_url
                        ] = updated_product

                        append_rows_csv(
                            checkpoint_dir / "products_detail.csv",
                            [updated_product],
                        )
                        append_rows_csv(
                            checkpoint_dir / "options_raw.csv",
                            option_rows,
                        )
                        append_rows_csv(
                            checkpoint_dir / "sku_result.csv",
                            sku_rows,
                        )

                        all_options.extend(option_rows)
                        all_skus.extend(sku_rows)

                        progress.advance(detail_task)

                all_products = [
                    updated_by_id.get(p.product_id or p.product_url, p)
                    for p in all_products
                ]

    finally:
        pool.close_all()

        log_info("[엑셀 저장]")

        try:
            save_excel(
                output_path=output_path,
                products=all_products,
                options=all_options,
                sku_rows=all_skus,
                category_tree=category_tree,
            )

        except Exception as exc:
            log_error(f"엑셀 저장 실패: {exc}")
            log_warn("엑셀 저장은 실패했지만 중간 CSV 파일은 남아 있습니다.")
            log_warn(f"중간 저장 폴더를 확인하세요: {checkpoint_dir.resolve()}")

    rule("완료")

    log_ok(f"상품 행 수: {len(all_products)}")
    log_ok(f"옵션 행 수: {len(all_options)}")
    log_ok(f"SKU 행 수: {len(all_skus)}")
    log_ok(f"저장 위치: {output_path.resolve()}")
    log_ok(f"중간 저장 위치: {checkpoint_dir.resolve()}")

    return output_path