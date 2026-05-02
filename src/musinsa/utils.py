from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import (
    BASE_BRAND_URL,
    GENDER_FILTER,
    KST,
    PRICE_MAX,
    PRICE_MIN,
    PRICE_RE,
    PRODUCT_ID_PATTERNS,
)


def now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_int_price(text: str) -> Optional[int]:
    """텍스트 안의 가격 후보 중 합리적 범위의 최댓값을 본가로 채택."""
    text = clean_text(text)
    candidates: List[int] = []
    for match in PRICE_RE.finditer(text):
        raw = match.group(1).replace(",", "")
        try:
            value = int(raw)
        except ValueError:
            continue
        if PRICE_MIN <= value <= PRICE_MAX:
            candidates.append(value)
    if not candidates:
        return None
    return max(candidates)


def parse_discount(text: str) -> Optional[str]:
    text = clean_text(text)
    match = re.search(r"(\d{1,3})\s*%", text)
    if not match:
        return None
    return f"{match.group(1)}%"


def extract_product_id(url: str) -> str:
    for pattern in PRODUCT_ID_PATTERNS:
        match = pattern.search(url or "")
        if match:
            return match.group(1)
    return ""


def ensure_gender_filter_url(url: str) -> str:
    """모든 무신사 URL에 gf=F 를 강제로 붙입니다."""
    url = clean_text(url)
    if not url:
        return ""

    parts = urlsplit(url)
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "gf"
    ]
    query_pairs.append(("gf", GENDER_FILTER))
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query_pairs, doseq=True),
            parts.fragment,
        )
    )


def build_category_url(category_code: str) -> str:
    return ensure_gender_filter_url(f"{BASE_BRAND_URL}?categoryCode={category_code}")


def make_sku_key(product_id: str, color: str, size: str) -> str:
    def normalize_part(value: str) -> str:
        value = clean_text(value)
        value = value.replace("/", "-")
        value = re.sub(r"\s+", "_", value)
        value = re.sub(r"[^0-9A-Za-z가-힣_\-]", "", value)
        return value or "UNKNOWN"

    return f"{normalize_part(product_id)}_{normalize_part(color)}_{normalize_part(size)}"


def dataclass_list_to_dicts(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    return [row.__dict__.copy() for row in rows]
