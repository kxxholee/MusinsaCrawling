from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

from bs4 import BeautifulSoup

from .browser import close_common_popups, safe_goto
from .config import (
    BASE_BRAND_URL,
    MAJOR_CODE_SEEDS,
    MAJOR_NAME_FALLBACK,
    MUSINSA_CATEGORY_INDEX_URL,
)
from .logger import log_info, log_ok, log_warn
from .utils import clean_text, ensure_gender_filter_url


def _extract_category_id_pairs(html: str) -> List[Tuple[str, str, str]]:
    """[data-category-id] 노드를 (code, full_name, section) 트리플로 모은다."""
    soup = BeautifulSoup(html, "html.parser")
    out: List[Tuple[str, str, str]] = []
    seen = set()
    for el in soup.select("[data-category-id]"):
        cid = clean_text(el.get("data-category-id", ""))
        if not cid or not cid.isdigit():
            continue
        if len(cid) not in (3, 6):
            continue
        cname = clean_text(el.get("data-category-name", ""))
        section = clean_text(el.get("data-section-name", ""))
        key = (cid, cname, section)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _split_major_sub_name(full_name: str) -> Tuple[str, str]:
    name = clean_text(full_name)
    if not name or name == "(not set)":
        return "", ""
    parts = [p.strip() for p in name.split("|") if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", parts[0] if parts else ""


def discover_category_tree(driver: Any) -> List[Dict[str, Any]]:
    """무신사 카테고리 네비게이션을 그때그때 긁어 트리를 만든다."""
    log_info("[카테고리 트리 발견] 대분류 코드 수집")

    seed_url = ensure_gender_filter_url(f"{BASE_BRAND_URL}?categoryCode=001")
    safe_goto(driver, seed_url)
    close_common_popups(driver)
    time.sleep(2.0)

    major_codes: List[str] = []
    seen_majors: set = set()
    for cid, _name, section in _extract_category_id_pairs(driver.page_source):
        if section == "menu_tab" and len(cid) == 3 and cid not in seen_majors:
            seen_majors.add(cid)
            major_codes.append(cid)

    if not major_codes:
        log_warn("menu_tab 발견 실패 → 시드 폴백 사용")
        major_codes = list(MAJOR_CODE_SEEDS)

    log_info(f"[카테고리 트리] 대분류 후보: {', '.join(major_codes)}")

    tree: List[Dict[str, Any]] = []
    for major_code in major_codes:
        index_url = ensure_gender_filter_url(MUSINSA_CATEGORY_INDEX_URL.format(code=major_code))
        if not safe_goto(driver, index_url):
            continue
        close_common_popups(driver)
        time.sleep(1.5)

        major_name = ""
        subs: Dict[str, str] = {}
        for cid, full_name, _section in _extract_category_id_pairs(driver.page_source):
            mname, sname = _split_major_sub_name(full_name)
            if cid == major_code and mname and not major_name:
                major_name = mname
            if len(cid) == 6 and cid.startswith(major_code) and sname:
                if cid not in subs:
                    subs[cid] = sname
                    if mname and not major_name:
                        major_name = mname

        if not subs:
            log_warn(f"{major_code}: 서브 0개 (스킵)")
            continue

        major_name = major_name or MAJOR_NAME_FALLBACK.get(major_code, major_code)
        subs_sorted = [{"code": c, "name": n} for c, n in sorted(subs.items())]
        tree.append(
            {
                "major_code": major_code,
                "major_name": major_name,
                "subs": subs_sorted,
            }
        )
        log_info(f"  - {major_code} {major_name}: 서브 {len(subs_sorted)}개")

    if tree:
        log_ok(f"[카테고리 트리] 대분류 {len(tree)}개, 서브 {sum(len(m['subs']) for m in tree)}개 확정")
    else:
        log_warn("서브카테고리를 한 개도 발견하지 못했습니다.")

    return tree


def category_tree_to_master_rows(tree: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for major in tree:
        for sub in major["subs"]:
            rows.append(
                {
                    "major_category": major["major_name"],
                    "major_code": major["major_code"],
                    "category_code": sub["code"],
                    "raw_category": sub["name"],
                }
            )
    return rows
