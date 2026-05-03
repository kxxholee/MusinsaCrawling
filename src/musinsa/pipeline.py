from __future__ import annotations

import asyncio
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.table import Table

from .categories import discover_category_tree
from .config import BRAND, DEFAULT_OUTPUT, GENDER_FILTER
from .detail import collect_detail_and_options
from .detail_api import DETAIL_CONCURRENCY, gather_details_async
from .driver_pool import DriverPool
from .excel import save_excel
from .listing import collect_products_from_category, gather_listings_async
from .logger import console, log_error, log_info, log_ok, log_warn, make_progress, rule
from .schemas import OptionRow, ProductRow, SkuRow


# 비동기 listing 동시성. workers (드라이버 수) 와 무관하게 HTTP-only 경로라
# 더 높은 값을 써도 안전하다. 너무 높이면 무신사가 rate-limit 가능.
LISTING_CONCURRENCY = 16


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


def make_major_task_description(
    major_code: str,
    major_name: str,
    raw_count: int,
    added_count: int,
    error_count: int,
) -> str:
    description = (
        f"{major_code} {major_name} "
        f"· 후보 {raw_count}개 "
        f"· 추가 {added_count}개"
    )

    if error_count:
        description += f" · 오류 {error_count}개"

    return description


def print_major_summary(major_stats: Dict[str, Dict[str, Any]]) -> None:
    table = Table(title="상위 카테고리별 목록 수집 결과")

    table.add_column("상위 코드", justify="center")
    table.add_column("상위 카테고리")
    table.add_column("하위 카테고리 수", justify="right")
    table.add_column("후보 수", justify="right")
    table.add_column("추가 상품 수", justify="right")
    table.add_column("중복 제외 수", justify="right")
    table.add_column("오류", justify="right")

    for major_code in sorted(major_stats.keys()):
        stat = major_stats[major_code]
        raw_count = int(stat["raw_count"])
        added_count = int(stat["added_count"])
        duplicate_count = max(raw_count - added_count, 0)

        table.add_row(
            major_code,
            str(stat["major_name"]),
            str(stat["sub_count"]),
            str(raw_count),
            str(added_count),
            str(duplicate_count),
            str(stat["error_count"]),
        )

    console.print(table)


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

    rule("무신사 스탠다드 여성 SKU 생성기 데모")

    log_info(f"브랜드: {BRAND}")
    log_info(f"여성 필터: gf={GENDER_FILTER} 고정")
    log_info("카테고리: 상위 3자리 카테고리별 진행률 표시")
    log_info(
        f"카테고리별 최대 상품 수: "
        f"{'전체' if max_products_resolved is None else max_products_resolved}"
    )
    log_info(f"옵션 수집: {'건너뜀' if skip_options else '실행'}")
    log_info(f"브라우저: {browser}")
    log_info(f"저장 파일: {output_path}")

    all_products: List[ProductRow] = []
    all_options: List[OptionRow] = []
    all_skus: List[SkuRow] = []

    workers_resolved = max(1, int(workers))

    log_info(f"병렬 워커: {workers_resolved}개")

    pool = DriverPool(size=workers_resolved, headless=headless, browser=browser)

    category_tree: List[Dict[str, Any]] = []
    major_stats: Dict[str, Dict[str, Any]] = {}

    try:
        with pool.borrow() as driver:
            category_tree = discover_category_tree(driver)

        if not category_tree:
            log_warn("카테고리 트리가 비어 있어 크롤을 중단합니다.")
            return output_path

        work_units: List[Dict[str, str]] = []

        for major in category_tree:
            subs = major["subs"]
            major_code = str(major.get("major_code") or "").strip()

            if not major_code and subs:
                major_code = str(subs[0].get("code", ""))[:3]

            major_name = str(major.get("major_name") or major_code).strip()

            # +1 = major-code (3자리) catch-all unit. 무신사는 상품을 major에만
            # 태깅하고 sub에 누락시키는 경우가 많아 (예: 상의 771 vs sub 합산 206),
            # 메이저 코드 한 번 더 호출해 글로벌 seen_product_ids 로 dedupe 한다.
            major_stats[major_code] = {
                "major_name": major_name,
                "sub_count": len(subs) + 1,
                "raw_count": 0,
                "added_count": 0,
                "error_count": 0,
            }

            for sub in subs:
                category_code = str(sub["code"])

                work_units.append(
                    {
                        "major_code": major_code,
                        "major_category": major_name,
                        "category_code": category_code,
                        "raw_category": str(sub["name"]),
                    }
                )

            if major_code:
                work_units.append(
                    {
                        "major_code": major_code,
                        "major_category": major_name,
                        "category_code": major_code,
                        "raw_category": f"{major_name}(미분류)",
                    }
                )

        with make_progress() as progress:
            major_tasks: Dict[str, int] = {}

            for major_code, stat in major_stats.items():
                major_tasks[major_code] = progress.add_task(
                    make_major_task_description(
                        major_code=major_code,
                        major_name=str(stat["major_name"]),
                        raw_count=0,
                        added_count=0,
                        error_count=0,
                    ),
                    total=int(stat["sub_count"]),
                )

            seen_product_ids: set = set()
            fallback_units: List[Dict[str, str]] = []

            def on_listing_done(
                unit: Dict[str, str],
                rows: List[ProductRow],
                api_confirmed: bool,
            ) -> None:
                major_code = unit["major_code"]
                stat = major_stats[major_code]
                task_id = major_tasks[major_code]

                if not api_confirmed and not rows:
                    fallback_units.append(unit)

                stat["raw_count"] += len(rows)
                added = 0

                for row in rows:
                    key = row.product_id or row.product_url

                    if not key or key in seen_product_ids:
                        continue

                    seen_product_ids.add(key)
                    all_products.append(row)
                    added += 1

                stat["added_count"] += added

                progress.update(
                    task_id,
                    description=make_major_task_description(
                        major_code=major_code,
                        major_name=str(stat["major_name"]),
                        raw_count=int(stat["raw_count"]),
                        added_count=int(stat["added_count"]),
                        error_count=int(stat["error_count"]),
                    ),
                )
                progress.advance(task_id)

            asyncio.run(
                gather_listings_async(
                    work_units=work_units,
                    max_products=max_products_resolved,
                    concurrency=LISTING_CONCURRENCY,
                    on_done=on_listing_done,
                )
            )

            # API 가 응답 자체를 안 준 work_unit 만 Selenium 폴백 (대개 0건).
            if fallback_units:
                log_warn(f"API 응답 실패 {len(fallback_units)}건 → Selenium 폴백")

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
                        for unit in fallback_units
                    }

                    for future in as_completed(futures):
                        unit = futures[future]
                        major_code = unit["major_code"]
                        stat = major_stats[major_code]
                        task_id = major_tasks[major_code]

                        try:
                            rows = future.result()
                        except Exception as exc:
                            stat["error_count"] += 1
                            log_error(
                                f"{unit['major_code']} / "
                                f"{unit['category_code']} 폴백 실패: {exc}"
                            )
                            continue

                        stat["raw_count"] += len(rows)
                        added = 0

                        for row in rows:
                            key = row.product_id or row.product_url

                            if not key or key in seen_product_ids:
                                continue

                            seen_product_ids.add(key)
                            all_products.append(row)
                            added += 1

                        stat["added_count"] += added

                        progress.update(
                            task_id,
                            description=make_major_task_description(
                                major_code=major_code,
                                major_name=str(stat["major_name"]),
                                raw_count=int(stat["raw_count"]),
                                added_count=int(stat["added_count"]),
                                error_count=int(stat["error_count"]),
                            ),
                        )

            log_ok(f"[목록 수집 완료] 중복 제거 후 상품 수: {len(all_products)}")

            if not skip_options and all_products:
                detail_task = progress.add_task(
                    "상세 페이지/옵션 수집",
                    total=len(all_products),
                )

                updated_by_id: Dict[str, ProductRow] = {}
                fallback_products: List[ProductRow] = []

                def on_detail_done(
                    product: ProductRow,
                    options: List[OptionRow],
                    skus: List[SkuRow],
                    api_ok: bool,
                ) -> None:
                    if not api_ok:
                        fallback_products.append(product)
                    # api_ok 인데 옵션이 비면 옵션이 정말 없는 상품 — fallback 안 시도.
                    updated_by_id[product.product_id or product.product_url] = product
                    all_options.extend(options)
                    all_skus.extend(skus)
                    progress.advance(detail_task)

                asyncio.run(
                    gather_details_async(
                        products=all_products,
                        concurrency=DETAIL_CONCURRENCY,
                        on_done=on_detail_done,
                    )
                )

                # API 가 5xx/네트워크로 실패한 상품만 Selenium 폴백 (대개 0건).
                if fallback_products:
                    log_warn(
                        f"옵션 API 실패 {len(fallback_products)}건 → Selenium 폴백"
                    )

                    with ThreadPoolExecutor(max_workers=workers_resolved) as executor:
                        futures = {
                            executor.submit(crawl_detail_unit, pool, product, delay): product
                            for product in fallback_products
                        }

                        for future in as_completed(futures):
                            src = futures[future]

                            try:
                                updated_product, option_rows, sku_rows = future.result()
                            except Exception as exc:
                                log_error(f"{src.product_id} 폴백 실패: {exc}")
                                continue

                            updated_by_id[
                                updated_product.product_id or updated_product.product_url
                            ] = updated_product
                            all_options.extend(option_rows)
                            all_skus.extend(sku_rows)

                all_products = [
                    updated_by_id.get(p.product_id or p.product_url, p)
                    for p in all_products
                ]

    finally:
        pool.close_all()

        log_info("[엑셀 저장]")

        save_excel(
            output_path=output_path,
            products=all_products,
            options=all_options,
            sku_rows=all_skus,
            category_tree=category_tree,
        )

    rule("완료")

    if major_stats:
        print_major_summary(major_stats)

    log_ok(f"상품 행 수: {len(all_products)}")
    log_ok(f"옵션 행 수: {len(all_options)}")
    log_ok(f"SKU 행 수: {len(all_skus)}")
    log_ok(f"저장 위치: {output_path.resolve()}")

    return output_path