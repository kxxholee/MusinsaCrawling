"""goods-detail.musinsa.com 옵션 API 기반 상세 수집 (Selenium 비의존).

listing.py 의 async 패턴을 그대로 미러: 단일 aiohttp.ClientSession +
asyncio.Semaphore + asyncio.gather. 색상은 product_name 의 [...] suffix 에서
추출 (무신사 스탠다드 우먼은 색상별로 별도 goods_no 패턴이라 colorCode →
한글명 매핑 테이블 불필요).
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp

from .schemas import OptionRow, ProductRow, SkuRow
from .text_parse import extract_color_from_text
from .utils import clean_text, ensure_gender_filter_url, make_sku_key


GOODS_OPTIONS_API_URL = "https://goods-detail.musinsa.com/api2/goods/{goods_no}/options"
GOODS_OPTIONS_TIMEOUT_SECONDS = 10
GOODS_OPTIONS_SLEEP_SECONDS = 0.05  # await 사이 미세 양보, rate-limit 방지
DETAIL_API_MAX_RETRIES = 1  # 5xx/네트워크 한정 1회 재시도

# 모듈 상단 상수: pipeline.py 가 import 해서 동시성 조절에 사용.
DETAIL_CONCURRENCY = 32

# product_name 끝의 "[다크 그레이]" 같은 색상 표기. 무신사 스탠다드 우먼은 색상별로
# 별도 goods_no 라 이 패턴이 신뢰도가 가장 높다.
COLOR_FROM_NAME_RE = re.compile(r"\[([^\[\]]+)\]\s*$")


def extract_color_from_product_name(product_name: str) -> str:
    """product_name 끝의 [색상] 표기에서 색상명을 추출. 없으면 빈 문자열."""
    if not product_name:
        return ""

    match = COLOR_FROM_NAME_RE.search(product_name)
    if not match:
        return ""

    return clean_text(match.group(1))


def _request_headers(goods_no: Any) -> Dict[str, str]:
    return {
        "Accept": "application/json,text/plain,*/*",
        "Origin": "https://www.musinsa.com",
        "Referer": f"https://www.musinsa.com/products/{goods_no}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
            "Gecko/20100101 Firefox/124.0"
        ),
    }


async def fetch_goods_options_payload(
    session: aiohttp.ClientSession,
    goods_no: Any,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """옵션 API 호출.

    반환:
        (payload, api_ok)
        - 200 + JSON 성공: (payload_dict, True)
        - 4xx (단종/존재 안 함): (None, True) — fallback 시도하지 않음
        - 5xx / 네트워크 / JSONDecode (재시도 후에도 실패): (None, False) — fallback 시그널
    """
    if goods_no is None or goods_no == "":
        return None, True

    url = GOODS_OPTIONS_API_URL.format(goods_no=goods_no)
    headers = _request_headers(goods_no)
    timeout = aiohttp.ClientTimeout(total=GOODS_OPTIONS_TIMEOUT_SECONDS)

    last_transient = False

    for attempt in range(DETAIL_API_MAX_RETRIES + 1):
        try:
            async with session.get(url, headers=headers, timeout=timeout) as response:
                if 400 <= response.status < 500:
                    return None, True

                if response.status >= 500:
                    last_transient = True
                    if attempt < DETAIL_API_MAX_RETRIES:
                        await asyncio.sleep(GOODS_OPTIONS_SLEEP_SECONDS * 2)
                        continue
                    return None, False

                payload = await response.json(content_type=None)
                return payload, True

        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
            last_transient = True
            if attempt < DETAIL_API_MAX_RETRIES:
                await asyncio.sleep(GOODS_OPTIONS_SLEEP_SECONDS * 2)
                continue
            return None, False

    # 도달 불가지만 타입 체커 안심용
    return None, not last_transient


def _resolve_color(product: ProductRow) -> str:
    color = extract_color_from_product_name(product.product_name)
    if color:
        return color

    # 폴백: 상품명 본문에 색상 키워드가 있는 경우 (드물게 [...] 누락된 신상품)
    return extract_color_from_text(product.product_name)


def _resolve_option_price(product: ProductRow, item: Dict[str, Any]) -> Optional[int]:
    """API 의 price 는 본가 대비 델타. 0 이면 본가 그대로."""
    delta = item.get("price")
    if not isinstance(delta, (int, float)):
        delta = 0

    base = product.price
    if base is None:
        return None

    return int(base) + int(delta)


def parse_options_payload(
    payload: Optional[Dict[str, Any]],
    product: ProductRow,
) -> Tuple[List[OptionRow], List[SkuRow]]:
    """옵션 API payload 를 OptionRow / SkuRow 로 변환.

    payload 가 None 이거나 data 가 비면 빈 리스트 반환 → 호출부에서 옵션 없음으로 간주.
    isDeleted 인 SKU 는 skip (단종은 sku_count 에서 제외).
    """
    options: List[OptionRow] = []
    skus: List[SkuRow] = []

    if not isinstance(payload, dict):
        return options, skus

    data = payload.get("data")
    if not isinstance(data, dict):
        return options, skus

    items = data.get("optionItems") or []
    if not isinstance(items, list):
        return options, skus

    color = _resolve_color(product)
    product_url = ensure_gender_filter_url(product.product_url)
    seen_combo: set = set()

    for item in items:
        if not isinstance(item, dict):
            continue

        if item.get("isDeleted"):
            continue

        size = clean_text(item.get("managedCode") or "")
        if not size:
            # optionValues[0].name 폴백 (사이즈 라벨이 managedCode 와 다른 경우)
            values = item.get("optionValues") or []
            if values and isinstance(values, list) and isinstance(values[0], dict):
                size = clean_text(values[0].get("name") or "")
        if not size:
            continue

        combo = (color, size)
        if combo in seen_combo:
            continue
        seen_combo.add(combo)

        option_status = "판매중" if item.get("activated") else "품절"
        option_price = _resolve_option_price(product, item)

        options.append(
            OptionRow(
                product_id=product.product_id,
                product_name=product.product_name,
                major_category=product.major_category,
                raw_category=product.raw_category,
                color=color,
                size=size,
                option_name=f"{color} / {size}".strip(" /"),
                option_status=option_status,
                option_price=option_price,
                stock_qty=None,
                product_url=product_url,
            )
        )

        skus.append(
            SkuRow(
                major_category=product.major_category,
                raw_category=product.raw_category,
                product_id=product.product_id,
                product_name=product.product_name,
                color=color,
                size=size,
                sku_key=make_sku_key(product.product_id, color, size),
                sku_status=option_status,
                product_url=product_url,
            )
        )

    return options, skus


# on_done 콜백 시그니처: (product, options, skus, api_ok) → None
DetailDoneCallback = Callable[[ProductRow, List[OptionRow], List[SkuRow], bool], None]


async def gather_details_async(
    products: List[ProductRow],
    concurrency: int = DETAIL_CONCURRENCY,
    on_done: Optional[DetailDoneCallback] = None,
) -> Dict[str, Tuple[ProductRow, List[OptionRow], List[SkuRow], bool]]:
    """모든 상품의 옵션 API 호출을 동시에 실행.

    listing.py 의 gather_listings_async 와 동일 패턴. on_done 콜백은
    이벤트 루프 (메인 스레드) 에서 호출되므로 rich.Progress 갱신에 안전.

    반환 dict key = product.product_id or product.product_url.
    """
    if not products:
        return {}

    semaphore = asyncio.Semaphore(max(1, concurrency))
    connector = aiohttp.TCPConnector(
        limit=max(1, concurrency * 2),
        limit_per_host=max(1, concurrency * 2),
    )

    results: Dict[str, Tuple[ProductRow, List[OptionRow], List[SkuRow], bool]] = {}

    async with aiohttp.ClientSession(connector=connector) as session:
        async def one(product: ProductRow) -> None:
            async with semaphore:
                payload, api_ok = await fetch_goods_options_payload(
                    session=session,
                    goods_no=product.product_id,
                )
                # await 사이 양보 (rate-limit 보호)
                await asyncio.sleep(GOODS_OPTIONS_SLEEP_SECONDS)

            options, skus = parse_options_payload(payload, product)

            key = product.product_id or product.product_url
            results[key] = (product, options, skus, api_ok)

            if on_done is not None:
                try:
                    on_done(product, options, skus, api_ok)
                except Exception:
                    pass

        tasks = [asyncio.create_task(one(p)) for p in products]
        await asyncio.gather(*tasks, return_exceptions=False)

    return results
