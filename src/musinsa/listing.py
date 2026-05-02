from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from selenium.common.exceptions import WebDriverException

from .browser import close_common_popups, safe_goto
from .config import BRAND, GENDER_FILTER, PRODUCT_ID_PATTERNS
from .logger import log_info, log_ok
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
                    return clean_text(re.sub(r"\s*상품상세로\s*이동\s*$", "", aria))
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
        text = root.get_text("\n", strip=True) if root else anchor.get_text("\n", strip=True)
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


def fetch_product_items_from_plp_api(
    category_code: str,
    max_products: Optional[int],
) -> List[Dict[str, Any]]:
    """무신사 PLP JSON API에서 브랜드 상품 목록을 수집한다."""
    items, _api_confirmed = fetch_product_items_from_plp_api_checked(
        category_code=category_code,
        max_products=max_products,
    )
    return items


def fetch_product_items_from_plp_api_checked(
    category_code: str,
    max_products: Optional[int],
) -> tuple[List[Dict[str, Any]], bool]:
    """무신사 PLP JSON API에서 수집하고, API 응답 성공 여부를 함께 반환한다."""
    items: List[Dict[str, Any]] = []
    seen = set()
    page = 1
    total_pages: Optional[int] = None
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

    while total_pages is None or page <= total_pages:
        params = {
            "gf": GENDER_FILTER,
            "sortCode": "NEW",
            "category": category_code,
            "brand": BRAND,
            "page": page,
            "size": PLP_API_PAGE_SIZE,
            "caller": "FLAGSHIP",
        }
        url = f"{PLP_API_URL}?{urlencode(params)}"
        request = Request(url, headers=headers)

        try:
            with urlopen(request, timeout=PLP_API_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except (OSError, URLError, json.JSONDecodeError) as exc:
            log_info(f"[목록 API 실패] categoryCode={category_code} page={page}: {exc}")
            return items, api_confirmed

        data = payload.get("data") if isinstance(payload, dict) else {}
        goods_list = data.get("list") if isinstance(data, dict) else []
        api_confirmed = True
        if not isinstance(goods_list, list) or not goods_list:
            break

        pagination = data.get("pagination") if isinstance(data, dict) else {}
        if isinstance(pagination, dict):
            try:
                total_pages = int(pagination.get("totalPages") or page)
            except (TypeError, ValueError):
                total_pages = page
            if not pagination.get("hasNext") and page >= total_pages:
                total_pages = page

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

        page += 1
        time.sleep(PLP_API_SLEEP_SECONDS)

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
            driver.execute_script("window.scrollTo(0, arguments[0]);", y + max(int(viewport * 0.9), 900))
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


def collect_products_from_category(
    driver: Any,
    major_category: str,
    category_code: str,
    max_products: Optional[int],
    max_scrolls: int,
    raw_category: str = "미확인",
) -> List[ProductRow]:
    category_url = build_category_url(category_code)
    log_info(f"[목록] {major_category} / {raw_category} / categoryCode={category_code}")

    raw_items, api_confirmed = fetch_product_items_from_plp_api_checked(
        category_code=category_code,
        max_products=max_products,
    )
    if raw_items:
        log_info(f"[목록 API] {raw_category}: 후보 {len(raw_items)}")
    elif api_confirmed:
        log_info(f"[목록 API] {raw_category}: 상품 0개 확인")
    else:
        log_info(f"[목록 API] {raw_category}: 실패/미확인 → Selenium 렌더링 수집")

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

            log_info(f"[목록 재시도] {raw_category}: 상품 링크 렌더링 대기 후에도 0개 → 새로고침")
            try:
                driver.refresh()
                time.sleep(1.0)
                close_common_popups(driver)
            except WebDriverException:
                safe_goto(driver, category_url)
                close_common_popups(driver)
            initial_items = wait_for_product_items(driver)

    rows: List[ProductRow] = []
    seen_ids_or_urls = set()
    crawl_date = now_kst_str()

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
            lines, product_id=product_id
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

    log_ok(f"[목록 완료] {raw_category}: 후보 {len(raw_items)} → 수집 {len(rows)}")
    return rows
