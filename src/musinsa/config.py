from __future__ import annotations

import re
from datetime import timezone, timedelta
from typing import Dict, List


BRAND = "musinsastandardwoman"
GENDER_FILTER = "F"
BASE_BRAND_URL = f"https://www.musinsa.com/brand/{BRAND}/products"

DEFAULT_OUTPUT = "musinsa_standard_female_sku.xlsx"

KST = timezone(timedelta(hours=9))

MUSINSA_CATEGORY_INDEX_URL = "https://www.musinsa.com/category/{code}"


MAJOR_NAME_FALLBACK: Dict[str, str] = {
    "001": "상의",
    "002": "아우터",
    "003": "바지",
    "020": "스커트",
    "022": "원피스",
    "100": "원피스/스커트",
    "101": "소품",
    "102": "모자",
    "103": "신발",
    "104": "가방",
    "005": "스포츠/레저",
}

MAJOR_CODE_SEEDS: List[str] = [
    "001", "002", "003", "020", "022", "100", "101", "102", "103", "104", "005",
]

COLOR_ALIASES: Dict[str, List[str]] = {
    "블랙": ["BLACK", "블랙"],
    "화이트": ["WHITE", "화이트"],
    "아이보리": ["IVORY", "아이보리"],
    "그레이": ["GRAY", "GREY", "그레이"],
    "멜란지": ["멜란지"],
    "네이비": ["NAVY", "네이비"],
    "스카이블루": ["스카이블루"],
    "라이트블루": ["라이트블루"],
    "블루": ["BLUE", "블루"],
    "베이지": ["BEIGE", "베이지"],
    "브라운": ["BROWN", "브라운"],
    "카키": ["KHAKI", "카키"],
    "그린": ["GREEN", "그린"],
    "핑크": ["PINK", "핑크"],
    "레드": ["RED", "레드"],
    "옐로우": ["YELLOW", "옐로우"],
    "퍼플": ["PURPLE", "퍼플"],
    "오렌지": ["ORANGE", "오렌지"],
    "차콜": ["CHARCOAL", "차콜"],
    "크림": ["CREAM", "크림"],
    "오트밀": ["OATMEAL", "오트밀"],
    "민트": ["MINT", "민트"],
    "데님": ["DENIM", "데님"],
}

SIZE_WORD_RE = re.compile(
    r"(?<![A-Z0-9])("
    r"XXS|XS|S|M|L|XL|XXL|XXXL|"
    r"FREE|ONE\s*SIZE|"
    r"W\d{2}|"
    r"\d{2,3}"
    r")(?![A-Z0-9])",
    re.IGNORECASE,
)

NUMERIC_SIZE_WHITELIST = {
    "24", "25", "26", "27", "28", "29", "30", "31", "32", "33", "34", "35", "36", "37", "38", "40", "42", "44",
    "85", "90", "95", "100", "105", "110", "115",
}

SIZE_NOISE_WORDS = ("원", "%", "만", "개", "리뷰", "쿠폰", "포인트", "회", "건", "별점", "평점")

OPTION_NOISE_WORDS = (
    "상품상세문의", "답변완료", "리뷰", "후기", "착용", "문의", "별점", "평점",
    "좋아요", "신고", "작성자", "사이즈 추천", "구매 후기",
    "상품명", "체형정보", "cm", "kg", "신체정보",
)
OPTION_DATE_RE = re.compile(r"\b(?:\d{2,4}[./-])?\d{1,2}[./-]\d{1,2}\b")

PRICE_RE = re.compile(r"(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,7})\s*원")

PRICE_MIN = 1_000
PRICE_MAX = 5_000_000

PRODUCT_ID_PATTERNS = [
    re.compile(r"/products/(\d+)"),
    re.compile(r"/goods/(\d+)"),
    re.compile(r"goods_no=(\d+)"),
    re.compile(r"goodsNo=(\d+)"),
]
