from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import aiohttp
from bs4 import BeautifulSoup
from selenium.common.exceptions import WebDriverException

from .browser import close_common_popups, safe_goto
from .config import BRAND, GENDER_FILTER, PRODUCT_ID_PATTERNS
from .schemas import ProductRow
from .text_parse import guess_product_name
from .utils import (
    build_category_url,
    clean_text,
    ensure_gender_filter_url,
    extract_product_id,
    now_kst_str,
    parse_discount,
    parse_int_price,
)


PRODUCT_RENDER_TIMEOUT_SECONDS = 12.0
PRODUCT_RENDER_POLL_SECONDS = 0.5
EMPTY_LISTING_RETRY_COUNT = 1

PLP_API_URL = "https://api.musinsa.com/api2/dp/v2/plp/goods"
PLP_API_PAGE_SIZE = 30
PLP_API_SLEEP_SECONDS = 0.2
PLP_API_TIMEOUT_SECONDS = 10

# totalPages / hasNext 값을 못 읽어도 무한 루프에 빠지지 않게 하는 안전장치입니다.
PLP_API_MAX_PAGES = 500

# API가 같은 페이지만 반복해서 주는 경우를 막기 위한 안전장치입니다.
PLP_API_MAX_NO_NEW_PAGES = 3


def parse_product_items_from_html(html: str) -> List[Dict[str, Any]]:
    """현재 DOM 스냅샷에서 상품 카드 후보를 BeautifulSoup으로 추출."""
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, Any]] = []
    seen = set()

    def is_product_href(href: str) -> bool:
        return any(pattern.search(href or "") for pattern in PRODUCT_ID_PATTERNS)

    def card_root(anchor: Any) -> Any:
        last_good = anchor.parent or anchor

        for parent in anchor.parents:
            if not getattr(parent, "name", None):
                continue

            try:
                ids = set()

                for a in parent.find_all("a", href=True):
                    pid = extract_product_id(a.get("href", ""))

                    if pid:
                        ids.add(pid)

                unique_ids = len(ids)

            except Exception:
                unique_ids = 1

            if unique_ids <= 1:
                last_good = parent

                if parent.name in ("body", "html"):
                    break

            else:
                break

        return last_good

    def extract_card_name(root: Any, anchor: Any) -> str:
        if root is not None:
            for img in root.find_all("img"):
                alt = clean_text(img.get("alt", ""))

                if alt and len(alt) >= 3 and not alt.startswith("좋아요"):
                    return alt

            for a in root.find_all("a"):
                aria = clean_text(a.get("aria-label", ""))

                if aria and "상품상세로 이동" in aria:
                    return clean_text(
                        re.sub(r"\s*상품상세로\s*이동\s*$", "", aria)
                    )

            span = root.select_one('span[data-mds="Typography"]')

            if span:
                txt = clean_text(span.get_text(" ", strip=True))

                if txt and len(txt) >= 3:
                    return txt

        aria = clean_text(anchor.get("aria-label", "") if anchor else "")

        if aria and aria != "상품 상세로 이동":
            return clean_text(re.sub(r"\s*상품상세로\s*이동\s*$", "", aria))

        return ""

    def image_url(root: Any) -> str:
        img = root.find("img") if root else None

        if not img:
            return ""

        srcset = clean_text(img.get("srcset", ""))

        if srcset:
            return clean_text(srcset.split(",")[0].split()[0])

        for attr in ("src", "data-src", "data-original"):
            value = clean_text(img.get(attr, ""))

            if value:
                return value

        return ""

    for anchor in soup.find_all("a", href=True):
        href = ensure_gender_filter_url(anchor.get("href", ""))

        if href.startswith("/"):
            href = ensure_gender_filter_url(f"https://www.musinsa.com{href}")

        if not is_product_href(href):
            continue

        if href in seen:
            continue

        seen.add(href)

        root = card_root(anchor)
        text = root.get_text("\n", strip=True) if root else anchor.get_text(
            "\n",
            strip=True,
        )
        lines = [clean_text(line) for line in text.split("\n") if clean_text(line)]

        results.append(
            {
                "product_url": href,
                "product_name": extract_card_name(root, anchor),
                "lines": lines,
                "card_text": " | ".join(lines),
                "image_url": image_url(root),
            }
        )

    return results


def _format_won(value: Any) -> str:
    try:
        return f"{int(value):,}원"

    except (TypeError, ValueError):
        return ""


def _api_item_to_listing_item(item: Dict[str, Any]) -> Dict[str, Any]:
    price_text = _format_won(item.get("normalPrice") or item.get("price"))
    sale_rate = item.get("saleRate") or item.get("couponSaleRate")
    sale_text = f"{sale_rate}%" if sale_rate else ""
    soldout_text = "품절" if item.get("isSoldOut") else "판매중"

    lines = [
        clean_text(item.get("goodsName", "")),
        price_text,
        sale_text,
        soldout_text,
        clean_text(item.get("displayGenderText", "")),
    ]
    lines = [line for line in lines if line]

    goods_url = clean_text(item.get("goodsLinkUrl", ""))
    goods_no = clean_text(item.get("goodsNo", ""))

    if not goods_url and goods_no:
        goods_url = f"https://www.musinsa.com/products/{goods_no}"

    return {
        "product_url": ensure_gender_filter_url(goods_url),
        "product_name": clean_text(item.get("goodsName", "")),
        "lines": lines,
        "card_text": " | ".join(lines),
        "image_url": clean_text(item.get("thumbnail", "")),
    }


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None

        return int(value)

    except (TypeError, ValueError):
        return None


def _find_int_by_keys(source: Dict[str, Any], keys: List[str]) -> Optional[int]:
    for key in keys:
        value = _safe_int(source.get(key))

        if value is not None:
            return value

    return None


def _find_bool_by_keys(source: Dict[str, Any], keys: List[str]) -> Optional[bool]:
    for key in keys:
        if key not in source:
            continue

        value = source.get(key)

        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            lowered = value.strip().lower()

            if lowered in ("true", "y", "yes", "1"):
                return True

            if lowered in ("false", "n", "no", "0"):
                return False

        if isinstance(value, int):
            return bool(value)

    return None


def _extract_pagination_info(data: Dict[str, Any]) -> tuple[Optional[int], Optional[int], Optional[bool]]:
    """API 응답 구조가 조금 바뀌어도 페이지 정보를 최대한 읽는다."""
    pagination = data.get("pagination")

    if not isinstance(pagination, dict):
        pagination = {}

    merged: Dict[str, Any] = {}
    merged.update(data)
    merged.update(pagination)

    total_pages = _find_int_by_keys(
        merged,
        [
            "totalPages",
            "totalPage",
            "pageTotal",
            "pageCount",
            "totalPageCount",
            "lastPage",
        ],
    )
    total_count = _find_int_by_keys(
        merged,
        [
            "totalCount",
            "total",
            "count",
            "totalElements",
            "totalItems",
            "goodsCount",
        ],
    )
    has_next = _find_bool_by_keys(
        merged,
        [
            "hasNext",
            "hasNextPage",
            "next",
            "isNext",
        ],
    )

    return total_pages, total_count, has_next


def fetch_product_items_from_plp_api_checked(
    category_code: str,
    max_products: Optional[int],
) -> tuple[List[Dict[str, Any]], bool]:
    """무신사 PLP JSON API에서 수집하고, API 응답 성공 여부를 함께 반환한다.

    page>=2 호출은 첫 응답의 pagination.nextPageUrl 에 담긴 hmacId 토큰을
    그대로 사용해야 한다. 토큰 없이 page 파라미터만 올리면 403 이 뜬다.
    """
    items: List[Dict[str, Any]] = []
    seen = set()
    page = 1
    total_pages: Optional[int] = None
    total_count: Optional[int] = None
    has_next: Optional[bool] = None
    no_new_page_count = 0
    api_confirmed = False

    headers = {
        "Accept": "application/json,text/plain,*/*",
        "Origin": "https://www.musinsa.com",
        "Referer": ensure_gender_filter_url(build_category_url(category_code)),
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
            "Gecko/20100101 Firefox/124.0"
        ),
    }

    initial_params = {
        "gf": GENDER_FILTER,
        "sortCode": "NEW",
        "brand": BRAND,
        "page": page,
        "size": PLP_API_PAGE_SIZE,
        "caller": "FLAGSHIP",
    }

    if category_code:
        initial_params["category"] = category_code

    next_url: Optional[str] = f"{PLP_API_URL}?{urlencode(initial_params)}"

    while next_url and page <= PLP_API_MAX_PAGES:
        request = Request(next_url, headers=headers)

        try:
            with urlopen(request, timeout=PLP_API_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))

        except (OSError, URLError, json.JSONDecodeError):
            return items, api_confirmed

        data = payload.get("data") if isinstance(payload, dict) else {}
        goods_list = data.get("list") if isinstance(data, dict) else []

        api_confirmed = True

        if not isinstance(goods_list, list) or not goods_list:
            break

        page_total_pages, page_total_count, page_has_next = _extract_pagination_info(data)

        if page_total_pages is not None:
            total_pages = page_total_pages

        if page_total_count is not None:
            total_count = page_total_count

        if page_has_next is not None:
            has_next = page_has_next

        pagination = data.get("pagination") if isinstance(data, dict) else None
        next_page_url = (
            clean_text(pagination.get("nextPageUrl", "")) if isinstance(pagination, dict) else ""
        )

        before_count = len(items)

        for api_item in goods_list:
            if not isinstance(api_item, dict):
                continue

            item = _api_item_to_listing_item(api_item)

            product_url = ensure_gender_filter_url(item.get("product_url", ""))
            product_id = extract_product_id(product_url)
            key = product_id or product_url

            if not key or key in seen:
                continue

            seen.add(key)
            items.append(item)

            if max_products is not None and len(items) >= max_products:
                return items, api_confirmed

            if total_count is not None and len(items) >= total_count:
                return items, api_confirmed

        if len(items) == before_count:
            no_new_page_count += 1

        else:
            no_new_page_count = 0

        if no_new_page_count >= PLP_API_MAX_NO_NEW_PAGES:
            break

        if total_pages is not None and page >= total_pages:
            break

        if has_next is False:
            break

        if total_pages is None and has_next is None and len(goods_list) < PLP_API_PAGE_SIZE:
            break

        if not next_page_url:
            break

        next_url = next_page_url
        page += 1
        time.sleep(PLP_API_SLEEP_SECONDS)

    return items, api_confirmed


async def fetch_product_items_from_plp_api_async(
    session: aiohttp.ClientSession,
    category_code: str,
    max_products: Optional[int],
) -> Tuple[List[Dict[str, Any]], bool]:
    """fetch_product_items_from_plp_api_checked 의 async 변형.

    수십~수백 개 work_unit 을 asyncio.gather + Semaphore 로 동시에 돌리기 위한 경로.
    페이지네이션 토큰(nextPageUrl) 처리는 sync 버전과 같다.
    """
    items: List[Dict[str, Any]] = []
    seen: set = set()
    page = 1
    total_pages: Optional[int] = None
    total_count: Optional[int] = None
    has_next: Optional[bool] = None
    no_new_page_count = 0
    api_confirmed = False

    headers = {
        "Accept": "application/json,text/plain,*/*",
        "Origin": "https://www.musinsa.com",
        "Referer": ensure_gender_filter_url(build_category_url(category_code)),
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
            "Gecko/20100101 Firefox/124.0"
        ),
    }

    initial_params: Dict[str, Any] = {
        "gf": GENDER_FILTER,
        "sortCode": "NEW",
        "brand": BRAND,
        "page": page,
        "size": PLP_API_PAGE_SIZE,
        "caller": "FLAGSHIP",
    }

    if category_code:
        initial_params["category"] = category_code

    next_url: Optional[str] = f"{PLP_API_URL}?{urlencode(initial_params)}"
    timeout = aiohttp.ClientTimeout(total=PLP_API_TIMEOUT_SECONDS)

    while next_url and page <= PLP_API_MAX_PAGES:
        try:
            async with session.get(next_url, headers=headers, timeout=timeout) as response:
                if response.status >= 400:
                    return items, api_confirmed

                payload = await response.json(content_type=None)

        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
            return items, api_confirmed

        data = payload.get("data") if isinstance(payload, dict) else {}
        goods_list = data.get("list") if isinstance(data, dict) else []

        api_confirmed = True

        if not isinstance(goods_list, list) or not goods_list:
            break

        page_total_pages, page_total_count, page_has_next = _extract_pagination_info(data)

        if page_total_pages is not None:
            total_pages = page_total_pages

        if page_total_count is not None:
            total_count = page_total_count

        if page_has_next is not None:
            has_next = page_has_next

        pagination = data.get("pagination") if isinstance(data, dict) else None
        next_page_url = (
            clean_text(pagination.get("nextPageUrl", "")) if isinstance(pagination, dict) else ""
        )

        before_count = len(items)

        for api_item in goods_list:
            if not isinstance(api_item, dict):
                continue

            item = _api_item_to_listing_item(api_item)
            product_url = ensure_gender_filter_url(item.get("product_url", ""))
            product_id = extract_product_id(product_url)
            key = product_id or product_url

            if not key or key in seen:
                continue

            seen.add(key)
            items.append(item)

            if max_products is not None and len(items) >= max_products:
                return items, api_confirmed

            if total_count is not None and len(items) >= total_count:
                return items, api_confirmed

        if len(items) == before_count:
            no_new_page_count += 1
        else:
            no_new_page_count = 0

        if no_new_page_count >= PLP_API_MAX_NO_NEW_PAGES:
            break

        if total_pages is not None and page >= total_pages:
            break

        if has_next is False:
            break

        if total_pages is None and has_next is None and len(goods_list) < PLP_API_PAGE_SIZE:
            break

        if not next_page_url:
            break

        next_url = next_page_url
        page += 1
        await asyncio.sleep(PLP_API_SLEEP_SECONDS)

    return items, api_confirmed


def wait_for_product_items(
    driver: Any,
    timeout_seconds: float = PRODUCT_RENDER_TIMEOUT_SECONDS,
    poll_seconds: float = PRODUCT_RENDER_POLL_SECONDS,
) -> List[Dict[str, Any]]:
    """상품 목록 JS/API 렌더링이 끝나 상품 링크가 DOM에 붙을 때까지 기다린다."""
    deadline = time.monotonic() + timeout_seconds
    last_items: List[Dict[str, Any]] = []

    while time.monotonic() < deadline:
        last_items = parse_product_items_from_html(driver.page_source)

        if last_items:
            return last_items

        time.sleep(poll_seconds)

    return last_items


def scroll_and_collect_products(
    driver: Any,
    max_scrolls: int,
    wait_ms: int = 1000,
    max_products: Optional[int] = None,
    initial_items: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """스크롤하며 매 DOM 스냅샷의 상품 후보를 누적."""
    items_by_key: Dict[str, Dict[str, Any]] = {}
    stable_count = 0
    last_height = 0
    last_seen_count = 0

    for index in range(max_scrolls + 1):
        if index == 0 and initial_items is not None:
            snapshot_items = initial_items

        else:
            snapshot_items = parse_product_items_from_html(driver.page_source)

        for item in snapshot_items:
            product_url = ensure_gender_filter_url(item.get("product_url", ""))
            product_id = extract_product_id(product_url)
            key = product_id or product_url

            if key and key not in items_by_key:
                items_by_key[key] = item

            if max_products is not None and len(items_by_key) >= max_products:
                break

        try:
            height = int(driver.execute_script("return document.body.scrollHeight || 0"))
            y = int(driver.execute_script("return window.scrollY || 0"))
            viewport = int(driver.execute_script("return window.innerHeight || 900"))

            driver.execute_script(
                "window.scrollTo(0, arguments[0]);",
                y + max(int(viewport * 0.9), 900),
            )

        except WebDriverException:
            break

        time.sleep(wait_ms / 1000)

        if index == 0:
            last_height = height
            last_seen_count = len(items_by_key)
            continue

        no_new_products = len(items_by_key) == last_seen_count
        same_height = height == last_height
        stable_count = stable_count + 1 if no_new_products and same_height else 0

        if stable_count >= 5:
            break

        last_height = height
        last_seen_count = len(items_by_key)

    return list(items_by_key.values())


def items_to_product_rows(
    raw_items: List[Dict[str, Any]],
    major_category: str,
    category_code: str,
    raw_category: str,
    max_products: Optional[int],
) -> List[ProductRow]:
    """API 또는 DOM 에서 모은 raw item 들을 ProductRow 로 변환."""
    rows: List[ProductRow] = []
    seen_ids_or_urls: set = set()
    crawl_date = now_kst_str()
    category_url = build_category_url(category_code)

    for item in raw_items:
        product_url = ensure_gender_filter_url(item.get("product_url", ""))
        product_id = extract_product_id(product_url)
        key = product_id or product_url

        if not key or key in seen_ids_or_urls:
            continue

        seen_ids_or_urls.add(key)

        lines = item.get("lines", []) or []
        card_text = clean_text(item.get("card_text", ""))
        product_name = clean_text(item.get("product_name", "")) or guess_product_name(
            lines,
            product_id=product_id,
        )
        price = parse_int_price(card_text)
        discount_rate = parse_discount(card_text)
        image_url = clean_text(item.get("image_url", ""))
        is_soldout = "품절" if "품절" in card_text else "판매중"

        rows.append(
            ProductRow(
                crawl_date=crawl_date,
                gender_filter=GENDER_FILTER,
                brand=BRAND,
                major_category=major_category,
                raw_category=raw_category,
                category_code=category_code,
                product_id=product_id,
                product_name=product_name,
                product_url=ensure_gender_filter_url(product_url),
                price=price,
                discount_rate=discount_rate,
                image_url=image_url,
                is_soldout=is_soldout,
                source_category_url=ensure_gender_filter_url(category_url),
            )
        )

        if max_products is not None and len(rows) >= max_products:
            break

    return rows


def collect_products_from_category(
    driver: Any,
    major_category: str,
    category_code: str,
    max_products: Optional[int],
    max_scrolls: int,
    raw_category: str = "미확인",
) -> List[ProductRow]:
    """단일 work_unit 동기 수집 — API 우선, 실패 시 Selenium 폴백."""
    category_url = build_category_url(category_code)

    raw_items, api_confirmed = fetch_product_items_from_plp_api_checked(
        category_code=category_code,
        max_products=max_products,
    )

    if not raw_items and not api_confirmed:
        if not safe_goto(driver, category_url):
            return []

        close_common_popups(driver)
        initial_items = wait_for_product_items(driver)

        for attempt in range(EMPTY_LISTING_RETRY_COUNT + 1):
            raw_items = scroll_and_collect_products(
                driver=driver,
                max_scrolls=max_scrolls,
                max_products=max_products,
                initial_items=initial_items,
            )

            if raw_items or attempt >= EMPTY_LISTING_RETRY_COUNT:
                break

            try:
                driver.refresh()
                time.sleep(1.0)
                close_common_popups(driver)

            except WebDriverException:
                safe_goto(driver, category_url)
                close_common_popups(driver)

            initial_items = wait_for_product_items(driver)

    return items_to_product_rows(
        raw_items=raw_items,
        major_category=major_category,
        category_code=category_code,
        raw_category=raw_category,
        max_products=max_products,
    )


async def gather_listings_async(
    work_units: List[Dict[str, str]],
    max_products: Optional[int],
    concurrency: int = 16,
    on_done: Optional[Any] = None,
) -> Dict[str, Tuple[List[ProductRow], bool]]:
    """모든 work_unit 의 PLP API 호출을 동시에 실행하고 ProductRow 리스트로 반환.

    반환값 key 는 work_unit["category_code"]. on_done 콜백이 주어지면
    각 unit 완료 시점에 (unit, rows, api_confirmed) 로 호출되어 메인이 진행률을
    바로 갱신할 수 있다. (이 콜백은 메인 스레드에서 실행됨)
    """
    if not work_units:
        return {}

    semaphore = asyncio.Semaphore(max(1, concurrency))
    connector = aiohttp.TCPConnector(
        limit=max(1, concurrency * 2),
        limit_per_host=max(1, concurrency * 2),
    )

    async with aiohttp.ClientSession(connector=connector) as session:
        async def one(unit: Dict[str, str]) -> Tuple[Dict[str, str], List[ProductRow], bool]:
            async with semaphore:
                items, api_confirmed = await fetch_product_items_from_plp_api_async(
                    session=session,
                    category_code=unit["category_code"],
                    max_products=max_products,
                )

            rows = items_to_product_rows(
                raw_items=items,
                major_category=unit["major_category"],
                category_code=unit["category_code"],
                raw_category=unit["raw_category"],
                max_products=max_products,
            )

            if on_done is not None:
                try:
                    on_done(unit, rows, api_confirmed)
                except Exception:
                    pass

            return unit, rows, api_confirmed

        tasks = [asyncio.create_task(one(u)) for u in work_units]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    return {unit["category_code"]: (rows, api_confirmed) for unit, rows, api_confirmed in results}