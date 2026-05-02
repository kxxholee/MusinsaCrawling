from __future__ import annotations

import re
from typing import Iterable, List, Tuple

from .config import (
    COLOR_ALIASES,
    NUMERIC_SIZE_WHITELIST,
    OPTION_DATE_RE,
    OPTION_NOISE_WORDS,
    SIZE_NOISE_WORDS,
    SIZE_WORD_RE,
)
from .utils import clean_text


def guess_product_name(lines: Iterable[str], product_id: str = "") -> str:
    """카드 본문 줄 중 상품명으로 보이는 한 줄을 고른다."""
    blocked_words = [
        "무신사", "MUSINSA", "STANDARD", "스탠다드",
        "원", "%", "쿠폰", "리뷰", "평점", "좋아요", "SALE",
        "무료배송", "배송", "품절",
        "멤버스데이", "단독", "옵션", "공용", "도착보장",
        "영상정보처리기기", "관리방침", "이용약관", "오프라인 전용",
    ]

    candidates: List[str] = []
    for line in lines:
        line = clean_text(line)
        if not line:
            continue
        if product_id and product_id in line:
            continue
        if any(word.lower() in line.lower() for word in blocked_words):
            continue
        if len(line) < 3:
            continue
        candidates.append(line)

    if candidates:
        candidates = sorted(candidates, key=lambda x: (abs(len(x) - 24), len(x)))
        return candidates[0]

    for line in lines:
        line = clean_text(line)
        if line and "원" not in line and "%" not in line:
            return line

    return ""


def normalize_size_token(token: str) -> str:
    token = clean_text(token).upper()
    token = token.replace("ONE SIZE", "ONE SIZE")
    return token


def extract_size_from_text(text: str) -> str:
    text = clean_text(text)
    if not text:
        return ""

    if any(noise in text for noise in SIZE_NOISE_WORDS):
        return ""

    for match in SIZE_WORD_RE.finditer(text):
        token = match.group(1)
        token_upper = normalize_size_token(token)

        if token_upper.isdigit():
            if token_upper in NUMERIC_SIZE_WHITELIST:
                return token_upper
            continue

        return token_upper

    return ""


def is_option_noise_text(text: str) -> bool:
    text = clean_text(text)
    if not text:
        return True
    if any(word in text for word in OPTION_NOISE_WORDS):
        return True
    if OPTION_DATE_RE.search(text):
        return True
    return False


def find_color_words(text: str) -> List[str]:
    """긴 alias 부터 매칭해 한·영 표기 중복을 막고 표준 키로 정규화."""
    text_clean = clean_text(text)
    if not text_clean:
        return []

    flat: List[Tuple[str, str]] = []
    for canonical, aliases in COLOR_ALIASES.items():
        for alias in aliases:
            flat.append((alias, canonical))
    flat.sort(key=lambda pair: -len(pair[0]))

    matched_spans: List[Tuple[int, int]] = []
    found: List[str] = []
    seen: set = set()

    for alias, canonical in flat:
        for match in re.finditer(re.escape(alias), text_clean, re.IGNORECASE):
            start, end = match.start(), match.end()
            if any(s <= start < e or s < end <= e for s, e in matched_spans):
                continue
            matched_spans.append((start, end))
            if canonical not in seen:
                found.append(canonical)
                seen.add(canonical)

    return found


def extract_color_from_text(text: str) -> str:
    colors = find_color_words(text)
    if not colors:
        return ""
    return "/".join(colors)


def option_status_from_text(text: str, disabled: bool = False) -> str:
    text = clean_text(text).lower()
    if disabled:
        return "품절"
    if "품절" in text or "sold out" in text or "soldout" in text:
        return "품절"
    if "재입고" in text and "알림" in text:
        return "품절"
    return "판매중"
