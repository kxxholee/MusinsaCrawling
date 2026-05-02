from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProductRow:
    crawl_date: str
    gender_filter: str
    brand: str
    major_category: str
    raw_category: str
    category_code: str
    product_id: str
    product_name: str
    product_url: str
    price: Optional[int]
    discount_rate: Optional[str]
    image_url: str
    is_soldout: str
    source_category_url: str


@dataclass
class OptionRow:
    product_id: str
    product_name: str
    major_category: str
    raw_category: str
    color: str
    size: str
    option_name: str
    option_status: str
    option_price: Optional[int]
    stock_qty: Optional[int]
    product_url: str


@dataclass
class SkuRow:
    major_category: str
    raw_category: str
    product_id: str
    product_name: str
    color: str
    size: str
    sku_key: str
    sku_status: str
    product_url: str
