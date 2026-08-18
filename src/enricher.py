"""Orchestrate dimension enrichment of a Step 1 eProcess output workbook.

Public API:
  get_blank_rows(wb)       → list[RowSpec]
  enrich_rows_iter(rows, force_retry)  → generator of (RowSpec, DimensionResult)
  apply_results(wb, rows, results)     → modified Workbook
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import PatternFill

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / 'adapters'))

from adapters.base import DimensionResult
from adapters.registry import fetch_for_brand
import cache as dim_cache

# ---------------------------------------------------------------------------
# Sheet / column constants (from Step 1 writer)
# ---------------------------------------------------------------------------

M10_SHEETS = ['Microwaves', 'DishWashers', 'Fridges & Freezers', 'Rangehoods', 'Cooktops', 'Ovens']

COL_BRAND   = 1
COL_PRODUCT = 2
COL_H       = 3
COL_W       = 4
COL_D       = 5
COL_LINK    = 6
COL_CONF    = 7   # Dimension Confidence
COL_SRC     = 8   # Dimension Source

_AMBER_FILL = PatternFill(start_color='FFECB3', end_color='FFECB3', fill_type='solid')
_NO_FILL    = PatternFill(fill_type=None)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class RowSpec:
    sheet:   str
    row_idx: int
    brand:   str
    sku:     str
    link:    str


# ---------------------------------------------------------------------------
# Reading the Step 1 workbook
# ---------------------------------------------------------------------------

def _cell_str(ws, row: int, col: int) -> str:
    v = ws.cell(row, col).value
    return str(v).strip() if v is not None else ''


def _is_blank_dim_row(ws, row_idx: int) -> bool:
    """True if all three dimension cells (H, W, D) are empty."""
    return all(
        not _cell_str(ws, row_idx, c)
        for c in (COL_H, COL_W, COL_D)
    )


def _is_header_row(ws, row_idx: int) -> bool:
    return (
        _cell_str(ws, row_idx, COL_BRAND)   == 'Brand' and
        _cell_str(ws, row_idx, COL_PRODUCT) == 'Product'
    )


def get_blank_rows(wb: Workbook) -> list[RowSpec]:
    """
    Parse a Step 1 output workbook and return all rows whose H/W/D are blank.
    Rows that already have dimensions filled are skipped.
    """
    rows: list[RowSpec] = []

    for sheet_name in M10_SHEETS:
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        in_block = False

        for row_idx in range(1, (ws.max_row or 0) + 1):
            brand   = _cell_str(ws, row_idx, COL_BRAND)
            product = _cell_str(ws, row_idx, COL_PRODUCT)

            if _is_header_row(ws, row_idx):
                in_block = True
                continue

            # Blank row signals end of block
            if not brand and not product:
                in_block = False
                continue

            # Data row: Product (col 2) is populated and it's not a header
            if in_block and product and brand != 'Brand':
                if _is_blank_dim_row(ws, row_idx):
                    rows.append(RowSpec(
                        sheet   = sheet_name,
                        row_idx = row_idx,
                        brand   = brand,
                        sku     = product,
                        link    = _cell_str(ws, row_idx, COL_LINK),
                    ))

    return rows


def summarise_blank_rows(rows: list[RowSpec]) -> dict:
    """Group blank rows by sheet and brand for pre-run display."""
    by_sheet: dict[str, int] = {}
    by_brand: dict[str, int] = {}
    for r in rows:
        by_sheet[r.sheet] = by_sheet.get(r.sheet, 0) + 1
        by_brand[r.brand] = by_brand.get(r.brand, 0) + 1
    return {'total': len(rows), 'by_sheet': by_sheet, 'by_brand': by_brand}


# ---------------------------------------------------------------------------
# Running enrichment (generator for live progress updates)
# ---------------------------------------------------------------------------

def enrich_rows_iter(
    rows: list[RowSpec],
    force_retry: bool = False,
) -> Generator[tuple[RowSpec, DimensionResult], None, None]:
    """
    Yield (RowSpec, DimensionResult) for each row, one at a time.

    Checks the cache first; only hits the network when necessary.
    Use force_retry=True to re-process 'Needs Review' and 'Not Found' rows.
    """
    for row in rows:
        if not row.link:
            result = DimensionResult(
                confidence='Not Found',
                reason='No product URL available',
            )
        elif not dim_cache.should_fetch(row.link, force_retry):
            result = dim_cache.get_cached(row.link) or DimensionResult(
                confidence='Not Found',
                reason='Cache miss (unexpected)',
            )
        else:
            result = fetch_for_brand(row.brand, row.link)
            dim_cache.store_result(row.link, result)

        yield row, result


# ---------------------------------------------------------------------------
# Writing results back to the workbook
# ---------------------------------------------------------------------------

def _ensure_confidence_headers(ws) -> None:
    """
    Add 'Dimension Confidence' and 'Dimension Source' to every header row
    in the sheet, if not already present.
    """
    from openpyxl.styles import Font
    bold = Font(bold=True)

    for row_idx in range(1, (ws.max_row or 0) + 1):
        if _is_header_row(ws, row_idx):
            if not ws.cell(row_idx, COL_CONF).value:
                cell_c = ws.cell(row_idx, COL_CONF, value='Dimension Confidence')
                cell_s = ws.cell(row_idx, COL_SRC,  value='Dimension Source')
                cell_c.font = bold
                cell_s.font = bold


def apply_results(
    wb: Workbook,
    rows: list[RowSpec],
    results: dict[tuple[str, int], DimensionResult],
) -> Workbook:
    """
    Write enrichment results into the workbook.

    results is keyed by (sheet_name, row_idx).
    Returns the modified workbook (same object, mutated in place).
    """
    # Add header columns to every M10 sheet
    for sheet_name in M10_SHEETS:
        if sheet_name in wb.sheetnames:
            _ensure_confidence_headers(wb[sheet_name])

    # Write per-row results
    for row in rows:
        result = results.get((row.sheet, row.row_idx))
        if result is None:
            continue

        ws = wb[row.sheet]

        # Dimensions
        ws.cell(row.row_idx, COL_H).value = result.height
        ws.cell(row.row_idx, COL_W).value = result.width
        ws.cell(row.row_idx, COL_D).value = result.depth

        # Remove amber fill for resolved rows; keep for unresolved
        fill = _NO_FILL if result.confidence == 'Resolved' else _AMBER_FILL
        for col in (COL_H, COL_W, COL_D):
            ws.cell(row.row_idx, col).fill = fill

        # Confidence + Source
        ws.cell(row.row_idx, COL_CONF).value = result.confidence
        ws.cell(row.row_idx, COL_SRC ).value = result.source_url

    return wb


def enrich_workbook(
    input_bytes: bytes,
    output_path: Path,
    force_retry: bool = False,
    progress_cb=None,
) -> tuple[list[RowSpec], dict[tuple[str, int], DimensionResult]]:
    """
    Full pipeline: read → enrich → write.

    input_bytes : raw .xlsx content (from Streamlit upload or file read)
    output_path : where to save the updated workbook
    force_retry : re-process non-resolved cached results
    progress_cb : optional callable(done: int, total: int, row: RowSpec, result)

    Returns (rows, results) for summary display.
    """
    import io
    wb = openpyxl.load_workbook(io.BytesIO(input_bytes))

    rows   = get_blank_rows(wb)
    total  = len(rows)
    results: dict[tuple[str, int], DimensionResult] = {}

    for i, (row, result) in enumerate(enrich_rows_iter(rows, force_retry)):
        results[(row.sheet, row.row_idx)] = result
        if progress_cb:
            progress_cb(i + 1, total, row, result)

    apply_results(wb, rows, results)
    wb.save(output_path)

    return rows, results
