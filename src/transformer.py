"""Main transformation pipeline: read → map → write."""

import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

# Allow running this module directly or importing from sibling files
sys.path.insert(0, str(Path(__file__).parent))

from reader import read_catalogue
from mapper import apply_mapping, load_mapping_rules, load_name_overrides
from writer import write_output, M10_SHEETS


def transform_files(
    input_paths: list[str | Path],
    output_path: str | Path,
    mapping_config: Optional[Path] = None,
    overrides_config: Optional[Path] = None,
) -> dict:
    """
    Transform one or more catalogue Excel files to eProcess format.

    Returns a summary dict:
      files              : {filename: {total_rows: int}}
      mapped_by_sheet    : {sheet_name: int}
      unmapped_by_reason : {reason_string: int}
      total_mapped       : int
      total_unmapped     : int
      missing_dimensions : int  (always equals total_mapped)
    """
    rules     = load_mapping_rules(mapping_config)
    overrides = load_name_overrides(overrides_config)

    # mapped[sheet][block] = [row_dict, ...]  (insertion order preserved)
    mapped: dict[str, dict[str, list]] = {sheet: {} for sheet in M10_SHEETS}
    unmapped: list[dict] = []

    summary: dict = {
        'files':               {},
        'mapped_by_sheet':     {sheet: 0 for sheet in M10_SHEETS},
        'unmapped_by_reason':  defaultdict(int),
        'total_mapped':        0,
        'total_unmapped':      0,
    }

    for filepath in input_paths:
        filepath = Path(filepath)
        df = read_catalogue(filepath)
        summary['files'][filepath.name] = {'total_rows': len(df)}

        for _, row in df.iterrows():
            row_dict = row.to_dict()

            # Pull out source fields (with safe fallbacks)
            brand    = row_dict.get('Brand', '')
            sku      = row_dict.get('Model / SKU', '')
            name     = row_dict.get('Product Name', '')
            pg       = row_dict.get('Product Group', '')
            subcat   = row_dict.get('Subcategory', '')
            link     = row_dict.get('_product_url', '')

            m10_sheet, block, reason = apply_mapping(row_dict, rules, overrides)

            if m10_sheet and block:
                sheet_blocks = mapped[m10_sheet]
                if block not in sheet_blocks:
                    sheet_blocks[block] = []
                sheet_blocks[block].append({
                    'Brand':   brand,
                    'Product': sku,
                    'Link':    link,
                })
                summary['mapped_by_sheet'][m10_sheet] += 1
                summary['total_mapped'] += 1
            else:
                unmapped.append({
                    'Brand':              brand,
                    'Product Group':      pg,
                    'Subcategory':        subcat,
                    'Model / SKU':        sku,
                    'Product Name':       name,
                    'Reason Not Mapped':  reason or 'Unknown',
                    'Link':               link,
                })
                summary['unmapped_by_reason'][reason or 'Unknown'] += 1
                summary['total_unmapped'] += 1

    # Every mapped row has blank H/W/D — report the expected count
    summary['missing_dimensions'] = summary['total_mapped']

    write_output(mapped, unmapped, output_path)
    return summary
