"""Map catalogue rows to M10 eProcess sheet / block destinations."""

import json
from pathlib import Path
from typing import Optional

_CONFIG_DIR = Path(__file__).parent.parent / 'config'


# ---------------------------------------------------------------------------
# Config loaders
# ---------------------------------------------------------------------------

def load_mapping_rules(config_path: Optional[Path] = None) -> list[dict]:
    """Load the ordered list of category mapping rules."""
    path = config_path or _CONFIG_DIR / 'category_mapping.json'
    with open(path, encoding='utf-8') as fh:
        data = json.load(fh)
    return data['rules']


def load_name_overrides(config_path: Optional[Path] = None) -> list[dict]:
    """Load secondary name-based override rules."""
    path = config_path or _CONFIG_DIR / 'name_overrides.json'
    with open(path, encoding='utf-8') as fh:
        data = json.load(fh)
    return data['overrides']


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def _contains_any(text: str, terms: list[str]) -> bool:
    """Return True if text contains any of the terms (case-insensitive)."""
    lo = text.lower()
    return any(t.lower() in lo for t in terms)


def _resolve_block(block: str, subcategory: str) -> str:
    """Expand the __USE_SUBCATEGORY__ sentinel, falling back to 'Uncategorised'."""
    if block == '__USE_SUBCATEGORY__':
        return subcategory.strip() or 'Uncategorised'
    return block


# ---------------------------------------------------------------------------
# Public mapping function
# ---------------------------------------------------------------------------

def apply_mapping(
    row: dict,
    rules: list[dict],
    overrides: list[dict],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Determine the M10 destination for a single catalogue row.

    Returns (m10_sheet, block, None) on success, or
            (None, None, reason_string) when no mapping is found.

    Lookup order:
      1. Secondary name-based overrides  (config/name_overrides.json)
      2. Main category mapping rules     (config/category_mapping.json)
      3. Unmapped with a descriptive reason
    """
    product_group = str(row.get('Product Group', '')).strip()
    subcategory   = str(row.get('Subcategory',   '')).strip()
    product_name  = str(row.get('Product Name',  '')).strip()
    sku           = str(row.get('Model / SKU',   '')).strip()

    pg_lower  = product_group.lower()
    sub_lower = subcategory.lower()

    # ------------------------------------------------------------------
    # 1. Name-based overrides (fires before main rules)
    # ------------------------------------------------------------------
    for override in overrides:
        ovr_pg = override.get('product_group', '').lower()
        if ovr_pg and ovr_pg != pg_lower:
            continue

        triggers = override.get('trigger_subcategory_contains', [])
        if triggers and not _contains_any(subcategory, triggers):
            continue

        # Concatenate the configured check-fields for keyword search
        combined = ' '.join(
            str(row.get(f, '')) for f in override.get('check_fields_global', [])
        ).lower()

        for name_rule in override.get('rules', []):
            check_text = ' '.join(
                str(row.get(f, '')) for f in name_rule.get('check_fields', ['Product Name', 'Model / SKU'])
            ).lower()
            if _contains_any(check_text, name_rule.get('keyword_contains', [])):
                block = _resolve_block(name_rule['block'], subcategory)
                return name_rule['m10_sheet'], block, None

    # ------------------------------------------------------------------
    # 2. Main category mapping rules (first match wins)
    # ------------------------------------------------------------------
    for rule in rules:
        if rule.get('product_group', '').lower() != pg_lower:
            continue

        sub_matches = rule.get('subcategory_contains', [])
        matched = (not sub_matches) or _contains_any(subcategory, sub_matches)

        if matched:
            block = _resolve_block(rule['block'], subcategory)
            return rule['m10_sheet'], block, None

    # ------------------------------------------------------------------
    # 3. Build a helpful "not mapped" reason
    # ------------------------------------------------------------------
    if not product_group:
        reason = 'Missing Product Group'
    elif not any(r.get('product_group', '').lower() == pg_lower for r in rules):
        reason = f"No M10 sheet for Product Group '{product_group}'"
    else:
        reason = f"No mapping for subcategory '{subcategory}' in Product Group '{product_group}'"

    return None, None, reason
