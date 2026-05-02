from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import pandas as pd

from .categories import category_tree_to_master_rows
from .schemas import OptionRow, ProductRow, SkuRow
from .utils import clean_text, dataclass_list_to_dicts, ensure_gender_filter_url


def make_model_key_from_values(
    major_category: Any,
    raw_category: Any,
    product_id: Any,
    product_url: Any,
) -> str:
    major_value = "" if pd.isna(major_category) else major_category
    raw_value = "" if pd.isna(raw_category) else raw_category
    product_id_value = "" if pd.isna(product_id) else product_id
    product_url_value = "" if pd.isna(product_url) else product_url
    product_identity = clean_text(product_id_value) or ensure_gender_filter_url(product_url_value)
    return "|".join(
        [
            clean_text(major_value),
            clean_text(raw_value),
            product_identity,
        ]
    )


def add_model_key_column(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        df["_model_key"] = []
        return df

    df = df.copy()
    df["_model_key"] = df.apply(
        lambda row: make_model_key_from_values(
            row.get("major_category", ""),
            row.get("raw_category", ""),
            row.get("product_id", ""),
            row.get("product_url", ""),
        ),
        axis=1,
    )
    return df


def normalize_url_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for column in ("product_url", "source_category_url"):
        if column in df.columns:
            df[column] = df[column].map(
                lambda value: "" if pd.isna(value) else ensure_gender_filter_url(value)
            )
    return df


def clean_model_name(raw_name: str) -> str:
    """보고서 표시용 모델명에서 브랜드 prefix와 ' - 사이즈 & 후기' 등을 제거."""
    if not raw_name:
        return ""
    name = clean_text(raw_name)
    name = re.sub(r"^무신사\s*스탠다드\s*\(MUSINSA\s*STANDARD\)\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*-\s*사이즈\s*&?\s*후기.*$", "", name)
    name = re.sub(r"\s*-\s*후기.*$", "", name)
    return name.strip()


def format_price_summary(prices: Iterable[Optional[int]]) -> str:
    """중분류 단위 가격을 '최고/최저/평균원' 형식으로."""
    valid = [int(p) for p in prices if p is not None]
    if not valid:
        return "-"
    return f"{max(valid):,} / {min(valid):,} / {int(round(sum(valid) / len(valid))):,}원"


def build_model_summary_df(
    products: List[ProductRow],
    sku_rows: List[SkuRow],
) -> pd.DataFrame:
    """모델(상품) 단위 요약 — SKU_요약 시트와 model_summary 시트 공통 입력."""
    columns = [
        "major_category", "raw_category", "product_id", "product_name",
        "color_count", "size_count", "sku_count",
        "available_sku_count", "soldout_sku_count",
        "price", "product_url",
    ]

    if not products:
        return pd.DataFrame(columns=columns)

    prod_df = normalize_url_columns(pd.DataFrame(dataclass_list_to_dicts(products)))
    sku_df = normalize_url_columns(pd.DataFrame(dataclass_list_to_dicts(sku_rows)))
    prod_df = add_model_key_column(prod_df)

    if sku_df.empty:
        prod_df = prod_df.assign(
            color_count=0, size_count=0, sku_count=0,
            available_sku_count=0, soldout_sku_count=0,
        )
    else:
        sku_df = add_model_key_column(sku_df)
        agg = sku_df.groupby("_model_key").agg(
            color_count=("color", "nunique"),
            size_count=("size", "nunique"),
            sku_count=("_model_key", "count"),
            available_sku_count=("sku_status", lambda s: int((s == "판매중").sum())),
            soldout_sku_count=("sku_status", lambda s: int((s == "품절").sum())),
        ).reset_index()
        prod_df = prod_df.merge(agg, on="_model_key", how="left")
        for col in ("color_count", "size_count", "sku_count", "available_sku_count", "soldout_sku_count"):
            prod_df[col] = prod_df[col].fillna(0).astype(int)

    return prod_df.reindex(columns=columns)


def build_sku_summary_sheet(
    ws: Any,
    products: List[ProductRow],
    sku_rows: List[SkuRow],
    category_tree: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """style.xlsx 양식의 SKU_요약 시트 — A열 대분류 / B,F,G,H,I열 중분류 단위 병합."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    headers = [
        "카테고리", "중분류", "모델명", "컬러 수", "사이즈 수",
        "SKU 개수", "SKU 비중", "최고/최저/평균가", "베이직/뉴베이직/트렌디",
    ]
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor="D9D9D9")
    header_font = Font(bold=True, size=11)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_wrap = Alignment(horizontal="left", vertical="center", wrap_text=True)

    for col_idx, label in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.font = header_font
        cell.alignment = center
        cell.fill = header_fill
        cell.border = border
    ws.row_dimensions[1].height = 30
    widths = {1: 18, 2: 16, 3: 38, 4: 8, 5: 8, 6: 12, 7: 12, 8: 26, 9: 22}
    for col_idx, width in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.freeze_panes = "A2"

    summary_df = build_model_summary_df(products, sku_rows)
    if summary_df.empty:
        return

    total_sku = int(summary_df["sku_count"].sum())

    tree = category_tree or []
    major_order: List[str] = [m["major_name"] for m in tree]
    raw_order_by_major: Dict[str, List[str]] = {
        m["major_name"]: [s["name"] for s in m["subs"]] for m in tree
    }

    for extra in summary_df["major_category"].dropna().unique().tolist():
        if extra and extra not in major_order:
            major_order.append(extra)
            raw_order_by_major.setdefault(extra, [])

    current_row = 2
    for major_cat in major_order:
        major_df = summary_df[summary_df["major_category"] == major_cat]

        major_total = int(major_df["sku_count"].sum())
        major_pct = major_total / total_sku if total_sku else 0

        raw_master = raw_order_by_major.get(major_cat, [])
        raw_in_data = list(major_df["raw_category"].unique())
        raw_sequence = (
            raw_master
            + [r for r in raw_in_data if r not in raw_master]
        )
        if not raw_sequence:
            continue

        major_start = current_row

        for raw_cat in raw_sequence:
            raw_df = major_df[major_df["raw_category"] == raw_cat].sort_values("product_id")

            raw_total = int(raw_df["sku_count"].sum())
            raw_pct = raw_total / total_sku if total_sku else 0
            price_summary = format_price_summary(raw_df["price"].tolist())
            style_summary = "미분류: 100%"

            raw_start = current_row
            if raw_df.empty:
                ws.cell(row=current_row, column=3, value="")
                ws.cell(row=current_row, column=4, value=0)
                ws.cell(row=current_row, column=5, value=0)
                current_row += 1
            else:
                for _, model in raw_df.iterrows():
                    ws.cell(row=current_row, column=3, value=clean_model_name(model["product_name"]))
                    ws.cell(row=current_row, column=4, value=int(model["color_count"]))
                    ws.cell(row=current_row, column=5, value=int(model["size_count"]))
                    current_row += 1
            raw_end = current_row - 1

            ws.cell(row=raw_start, column=2, value=raw_cat)
            ws.cell(row=raw_start, column=6, value=raw_total)
            pct_cell = ws.cell(row=raw_start, column=7, value=raw_pct)
            pct_cell.number_format = "0.00%"
            ws.cell(row=raw_start, column=8, value=price_summary)
            ws.cell(row=raw_start, column=9, value=style_summary)

            if raw_end > raw_start:
                for col in (2, 6, 7, 8, 9):
                    ws.merge_cells(
                        start_row=raw_start, end_row=raw_end,
                        start_column=col, end_column=col,
                    )

        major_end = current_row - 1
        if major_end >= major_start:
            ws.cell(
                row=major_start,
                column=1,
                value=f"{major_cat}\n(총 SKU: {major_total:,} / {major_pct:.1%})",
            )
            if major_end > major_start:
                ws.merge_cells(
                    start_row=major_start, end_row=major_end,
                    start_column=1, end_column=1,
                )

    last_row = current_row - 1
    if last_row >= 2:
        for row in ws.iter_rows(min_row=2, max_row=last_row, min_col=1, max_col=9):
            for cell in row:
                cell.border = border
                cell.alignment = left_wrap if cell.column == 3 else center


def adjust_excel_format(output_path: Path, skip_sheets: Optional[Set[str]] = None) -> None:
    """openpyxl로 raw 시트들에 헤더 스타일·자동필터·열 너비 적용. SKU_요약은 건드리지 않음."""
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError:
        return

    skip = skip_sheets or set()
    wb = load_workbook(output_path)

    header_fill = PatternFill("solid", fgColor="EDEDED")
    header_font = Font(bold=True)

    for ws in wb.worksheets:
        if ws.title in skip:
            continue
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for column_cells in ws.columns:
            max_len = 0
            col_letter = column_cells[0].column_letter
            for cell in column_cells:
                value = "" if cell.value is None else str(cell.value)
                max_len = max(max_len, len(value))
            ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 45)

    wb.save(output_path)


def save_excel(
    output_path: Path,
    products: List[ProductRow],
    options: List[OptionRow],
    sku_rows: List[SkuRow],
    category_tree: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """SKU_요약(보고서형)을 맨 앞에, 모델/원본 시트를 뒤에 둔다."""
    from openpyxl import load_workbook

    output_path.parent.mkdir(parents=True, exist_ok=True)

    master_rows = category_tree_to_master_rows(category_tree or [])
    category_df = pd.DataFrame(
        master_rows,
        columns=["major_category", "major_code", "category_code", "raw_category"],
    )
    products_df = normalize_url_columns(pd.DataFrame(dataclass_list_to_dicts(products)))
    options_df = normalize_url_columns(pd.DataFrame(dataclass_list_to_dicts(options)))
    sku_df = normalize_url_columns(pd.DataFrame(dataclass_list_to_dicts(sku_rows)))
    model_summary_df = normalize_url_columns(build_model_summary_df(products, sku_rows))

    products_df = products_df.reindex(columns=list(ProductRow.__annotations__.keys()))
    options_df = options_df.reindex(columns=list(OptionRow.__annotations__.keys()))
    sku_df = sku_df.reindex(columns=list(SkuRow.__annotations__.keys()))

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        model_summary_df.to_excel(writer, sheet_name="model_summary", index=False)
        products_df.to_excel(writer, sheet_name="products_raw", index=False)
        options_df.to_excel(writer, sheet_name="options_raw", index=False)
        sku_df.to_excel(writer, sheet_name="sku_result", index=False)
        category_df.to_excel(writer, sheet_name="category_master", index=False)

    wb = load_workbook(output_path)
    summary_ws = wb.create_sheet("SKU_요약", 0)
    build_sku_summary_sheet(summary_ws, products, sku_rows, category_tree=category_tree)
    wb.save(output_path)

    adjust_excel_format(output_path, skip_sheets={"SKU_요약"})
