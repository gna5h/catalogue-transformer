"""Read brand master catalogue Excel files into a normalised DataFrame."""

import pandas as pd
from pathlib import Path


def _cell_to_str(val) -> str:
    """Convert a cell value to a clean string, handling NaN and float-ints."""
    if val is None:
        return ''
    try:
        if pd.isna(val):
            return ''
    except (TypeError, ValueError):
        pass
    # Excel stores numeric-looking SKUs as floats; strip the '.0'
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val).strip()


def _find_product_url_column(columns: list[str]) -> str | None:
    """Return the first column header whose name ends with 'Product URL'."""
    for col in columns:
        if col.strip().lower().endswith('product url'):
            return col
    return None


def read_catalogue(filepath: str | Path) -> pd.DataFrame:
    """
    Read a single brand master catalogue workbook.

    Expected layout (first sheet):
      Row 1 : merged title banner  — skipped
      Row 2 : merged subtitle      — skipped
      Row 3 : blank                — skipped
      Row 4 : column headers
      Row 5+: data rows

    Returns a DataFrame where every cell is a stripped string.
    Adds two synthetic columns:
      _product_url  — value from whichever header ends with "Product URL"
      _source_file  — the filename (for diagnostics)
    """
    filepath = Path(filepath)

    # header=3 → use row index 3 (0-based) = row 4 (1-based) as the header
    raw = pd.read_excel(
        filepath,
        header=3,
        sheet_name=0,
        engine='openpyxl',
        dtype=object,   # keep everything as Python objects, not numpy types
    )

    # Normalise column names
    raw.columns = [_cell_to_str(c) for c in raw.columns]

    # Drop completely empty rows
    raw = raw.dropna(how='all').reset_index(drop=True)

    # Convert every cell to a clean string
    for col in raw.columns:
        raw[col] = raw[col].apply(_cell_to_str)

    # Drop rows where every value is empty after conversion
    raw = raw[raw.apply(lambda r: any(v for v in r.values), axis=1)].reset_index(drop=True)

    # Locate the dynamic product URL column (header ends with "Product URL")
    url_col = _find_product_url_column(list(raw.columns))
    raw['_product_url'] = raw[url_col] if url_col else ''

    raw['_source_file'] = filepath.name

    return raw
