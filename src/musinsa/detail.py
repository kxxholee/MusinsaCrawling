from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from bs4 import BeautifulSoup

from .browser import close_common_popups, safe_goto
from .schemas import OptionRow, ProductRow, SkuRow
from .text_parse import (
    extract_color_from_text,
    extract_size_from_text,
    is_option_noise_text,
    option_status_from_text,
)
from .utils import clean_text, ensure_gender_filter_url, make_sku_key, parse_int_price


def get_page_body_text(driver: Any) -> str:
    try:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        body = soup.find("body")
        return clean_text(body.get_text(" ", strip=True) if body else soup.get_text(" ", strip=True))
    except Exception:
        return ""


def get_detail_title(driver: Any, fallback: str) -> str:
    """상세 페이지에서 상품명을 다시 확인합니다."""
    try:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        meta_title = soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="title"]')
        title = clean_text(meta_title.get("content", "") if meta_title else "")
        if not title:
            h1 = soup.find("h1")
            title = clean_text(h1.get_text(" ", strip=True) if h1 else "")
        if not title:
            title = clean_text(driver.title)
    except Exception:
        title = ""

    title = re.sub(r"\s*\|\s*무신사.*$", "", title).strip()
    title = re.sub(r"\s*-\s*무신사.*$", "", title).strip()
    return title or fallback


def get_breadcrumb_like_text(driver: Any) -> str:
    """카테고리 추정 시 본문 전체보다 빵부스러기 영역을 먼저 본다."""
    try:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        chunks: List[str] = []
        selectors = [
            "nav",
            '[class*="breadcrumb"]',
            '[class*="Breadcrumb"]',
            '[class*="category"]',
            '[class*="Category"]',
        ]
        for selector in selectors:
            for element in soup.select(selector):
                text = clean_text(element.get_text(" ", strip=True))
                if text and len(text) < 1000:
                    chunks.append(text)
        return clean_text("\n".join(chunks))
    except Exception:
        return ""


def collect_option_text_candidates(driver: Any) -> List[Dict[str, Any]]:
    """옵션처럼 보이는 텍스트를 의도적으로 넓게 모은 뒤 파이썬에서 거른다."""
    try:
        soup = BeautifulSoup(driver.page_source, "html.parser")
    except Exception:
        return []

    selectors = [
        "button",
        "option",
        "li",
        '[role="option"]',
        '[class*="option"]',
        '[class*="Option"]',
        '[data-testid*="option"]',
        '[data-test*="option"]',
    ]
    nodes = []
    for selector in selectors:
        try:
            nodes.extend(soup.select(selector))
        except Exception:
            pass

    cleaned: List[Dict[str, Any]] = []
    seen = set()
    for node in nodes:
        text = clean_text(node.get_text(" ", strip=True))
        if not text or len(text) > 80:
            continue
        if is_option_noise_text(text):
            continue

        outer_html = str(node)
        disabled = (
            node.has_attr("disabled")
            or node.get("aria-disabled") == "true"
            or bool(re.search(r"disabled|soldout|sold-out|품절", outer_html, re.IGNORECASE))
        )

        context_parts: List[str] = []
        cur = node
        for _ in range(4):
            if not cur:
                break
            context_parts.extend(cur.get("class", []))
            context_parts.append(clean_text(cur.get("id", "")))
            context_parts.append(clean_text(cur.get("aria-label", "")))
            context_parts.append(clean_text(cur.get("data-testid", "")))
            context_parts.append(clean_text(cur.get("data-test", "")))
            cur = cur.parent
        context_text = clean_text(" ".join(context_parts))
        is_purchase_option = bool(
            re.search(r"구매옵션|선택|옵션|option|Option|select|Select", f"{text} {context_text}")
            and not re.search(r"상품상세문의|답변완료|리뷰|후기|착용", text)
        )

        key = (text, disabled, is_purchase_option)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(
            {
                "text": text,
                "disabled": disabled,
                "is_purchase_option": is_purchase_option,
            }
        )

    return cleaned


def parse_options_from_candidates(
    product_id: str,
    product_name: str,
    major_category: str,
    raw_category: str,
    product_url: str,
    candidates: List[Dict[str, Any]],
) -> Tuple[List[OptionRow], List[SkuRow]]:
    """한 라벨에서 색상+사이즈가 같이 확인되는 후보만 SKU로 계산한다."""
    option_rows: List[OptionRow] = []
    sku_rows: List[SkuRow] = []

    normalized_url = ensure_gender_filter_url(product_url)
    ordered_candidates = sorted(
        candidates,
        key=lambda item: 0 if item.get("is_purchase_option", False) else 1,
    )
    purchase_candidates = [
        item for item in ordered_candidates
        if item.get("is_purchase_option", False)
    ]
    candidate_groups = [purchase_candidates, ordered_candidates] if purchase_candidates else [ordered_candidates]

    for group in candidate_groups:
        combo_seen = set()
        group_option_rows: List[OptionRow] = []

        for item in group:
            text = clean_text(item.get("text", ""))
            disabled = bool(item.get("disabled", False))

            if is_option_noise_text(text):
                continue

            size = extract_size_from_text(text)
            color = extract_color_from_text(text)
            status = option_status_from_text(text, disabled=disabled)

            if not (size and color):
                continue

            key = (color, size)
            if key in combo_seen:
                continue
            combo_seen.add(key)

            group_option_rows.append(
                OptionRow(
                    product_id=product_id,
                    product_name=product_name,
                    major_category=major_category,
                    raw_category=raw_category,
                    color=color,
                    size=size,
                    option_name=text,
                    option_status=status,
                    option_price=parse_int_price(text),
                    stock_qty=None,
                    product_url=normalized_url,
                )
            )

        if group_option_rows:
            option_rows = group_option_rows
            break

    if option_rows:
        for opt in option_rows:
            sku_rows.append(
                SkuRow(
                    major_category=major_category,
                    raw_category=raw_category,
                    product_id=product_id,
                    product_name=product_name,
                    color=opt.color,
                    size=opt.size,
                    sku_key=make_sku_key(product_id, opt.color, opt.size),
                    sku_status=opt.option_status,
                    product_url=normalized_url,
                )
            )
        return option_rows, sku_rows

    diagnostic_seen = set()
    for item in ordered_candidates:
        text = clean_text(item.get("text", ""))
        if is_option_noise_text(text):
            continue

        color = extract_color_from_text(text) or "색상 미확인"
        size = extract_size_from_text(text)
        if not size and color == "색상 미확인":
            continue
        size = size or "사이즈 미확인"

        key = (color, size, text)
        if key in diagnostic_seen:
            continue
        diagnostic_seen.add(key)

        option_rows.append(
            OptionRow(
                product_id=product_id,
                product_name=product_name,
                major_category=major_category,
                raw_category=raw_category,
                color=color,
                size=size,
                option_name=text,
                option_status="옵션 후보",
                option_price=parse_int_price(text),
                stock_qty=None,
                product_url=normalized_url,
            )
        )

    if not option_rows:
        option_rows.append(
            OptionRow(
                product_id=product_id,
                product_name=product_name,
                major_category=major_category,
                raw_category=raw_category,
                color="색상 미확인",
                size="사이즈 미확인",
                option_name="옵션 미확인",
                option_status="옵션 미확인",
                option_price=None,
                stock_qty=None,
                product_url=normalized_url,
            )
        )

    return option_rows, sku_rows


def collect_detail_and_options(
    driver: Any,
    product: ProductRow,
) -> Tuple[ProductRow, List[OptionRow], List[SkuRow]]:
    if not product.product_url:
        return product, [], []

    product_url = ensure_gender_filter_url(product.product_url)
    source_category_url = ensure_gender_filter_url(product.source_category_url)

    if not safe_goto(driver, product_url):
        return product, [], []

    close_common_popups(driver)

    product_name = get_detail_title(driver, fallback=product.product_name)
    body_text = get_page_body_text(driver)
    raw_category = product.raw_category or "미확인"

    price = product.price or parse_int_price(body_text)

    updated_product = ProductRow(
        crawl_date=product.crawl_date,
        gender_filter=product.gender_filter,
        brand=product.brand,
        major_category=product.major_category,
        raw_category=raw_category,
        category_code=product.category_code,
        product_id=product.product_id,
        product_name=product_name,
        product_url=product_url,
        price=price,
        discount_rate=product.discount_rate,
        image_url=product.image_url,
        is_soldout=product.is_soldout,
        source_category_url=source_category_url,
    )

    candidates = collect_option_text_candidates(driver)
    option_rows, sku_rows = parse_options_from_candidates(
        product_id=updated_product.product_id,
        product_name=updated_product.product_name,
        major_category=updated_product.major_category,
        raw_category=updated_product.raw_category,
        product_url=updated_product.product_url,
        candidates=candidates,
    )

    return updated_product, option_rows, sku_rows
