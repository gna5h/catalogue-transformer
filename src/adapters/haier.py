"""Haier NZ dimension adapter.

Strategy (in priority order):
  1. "Product dimensions" HTML table — find table under matching heading.
  2. Free-text scan — regex over page text for axis-value patterns.

Platform note: haier.co.nz runs on Salesforce Commerce Cloud / Demandware,
the same platform as fisherpaykel.com. Page structure and dimension table
format are identical. This adapter is independently implemented — it shares
no imports or logic with fp.py; each file is independently editable.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseAdapter, DimensionResult, fetch_url

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PLAUSIBLE_MM = (200, 2500)

_EXCLUDE_RE = re.compile(
    r'\b(packing|shipping|gross|cutout|cut[\s\-]out|cavity|clearance)\b',
    re.IGNORECASE,
)

# Matches "Height 820-880mm", "Width 597mm", "Depth 574mm" in page text.
# \s*[-\u2013]\s* handles ranges with or without spaces around the hyphen.
_FREE_TEXT_RE = re.compile(
    r'\b(Height|Width|Depth)\s+(\d[\d\s\-\u2013]*mm?)',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clean_value(raw: str) -> Optional[str]:
    """Normalise a dimension string to a plain number (mm).

    Range  "820-880mm" or "820 - 880mm"  → max value → "880"
    Single "597mm" or "597"              → "597"
    cm values are converted to mm.
    Returns None if the string does not match a recognised pattern.
    """
    raw = raw.strip()
    # Range: "820-880mm", "857 - 917mm", "857 – 917mm"
    m = re.match(r'^(\d+)\s*[-\u2013]\s*(\d+)\s*(mm|cm)?\s*$', raw, re.IGNORECASE)
    if m:
        lo, hi, unit = m.groups()
        if unit and unit.lower() == 'cm':
            return str(max(int(lo), int(hi)) * 10)
        return str(max(int(lo), int(hi)))
    # Single: "597mm", "597 mm", "597"
    m = re.match(r'^(\d+)\s*(mm|cm)?\s*$', raw, re.IGNORECASE)
    if m:
        n, unit = m.groups()
        if unit and unit.lower() == 'cm':
            return str(int(n) * 10)
        return n
    return None


def _is_excluded_label(label: str) -> bool:
    return bool(_EXCLUDE_RE.search(label))


def _from_dim_table(soup: BeautifulSoup, url: str) -> Optional[DimensionResult]:
    """Strategy 1: find a heading 'Product dimensions' and parse the next <table>."""
    heading = None
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div', 'span']):
        if tag.get_text(strip=True).lower() == 'product dimensions':
            heading = tag
            break
    if heading is None:
        return None

    table = heading.find_next('table')
    if table is None:
        return None

    h = w = d = None
    raw_parts: list[str] = []
    for row in table.find_all('tr'):
        cells = row.find_all(['td', 'th'])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True)
        value = cells[1].get_text(strip=True)
        if not label or not value:
            continue
        if _is_excluded_label(label):
            continue
        cleaned = _clean_value(value)
        if cleaned is None:
            continue
        raw_parts.append(f'{label}: {value}')
        label_lower = label.lower()
        if 'height' in label_lower and h is None:
            h = cleaned
        elif 'width' in label_lower and w is None:
            w = cleaned
        elif 'depth' in label_lower and d is None:
            d = cleaned

    return _assemble(h, w, d, url, ' | '.join(raw_parts))


def _from_free_text(soup: BeautifulSoup, url: str) -> Optional[DimensionResult]:
    """Strategy 2: regex scan over all page text for axis-value patterns."""
    text = soup.get_text(' ')
    h = w = d = None
    raw_parts: list[str] = []
    for m in _FREE_TEXT_RE.finditer(text):
        axis = m.group(1).lower()
        value_raw = m.group(2).strip()
        cleaned = _clean_value(value_raw)
        if cleaned is None:
            continue
        raw_parts.append(f'{axis}: {value_raw}')
        if axis == 'height' and h is None:
            h = cleaned
        elif axis == 'width' and w is None:
            w = cleaned
        elif axis == 'depth' and d is None:
            d = cleaned
    return _assemble(h, w, d, url, ' | '.join(raw_parts))


def _apply_plausibility(result: DimensionResult) -> DimensionResult:
    """Downgrade to Needs Review if any dimension value falls outside 200–2500 mm."""
    lo, hi = _PLAUSIBLE_MM
    issues: list[str] = []
    for axis, val in [('height', result.height), ('width', result.width), ('depth', result.depth)]:
        if val is None:
            continue
        for n in (int(x) for x in re.findall(r'\d+', val)):
            if not (lo <= n <= hi):
                issues.append(f'{axis}={val} out of range [{lo},{hi}]')
                break
    if issues:
        return DimensionResult(
            height=result.height,
            width=result.width,
            depth=result.depth,
            confidence='Needs Review',
            source_url=result.source_url,
            raw_text=result.raw_text,
            reason='; '.join(issues),
        )
    return result


def _assemble(
    h: Optional[str], w: Optional[str], d: Optional[str],
    url: str, raw: str,
) -> Optional[DimensionResult]:
    """Map H/W/D values to a DimensionResult, or None if all axes are absent."""
    if h and w and d:
        return DimensionResult(height=h, width=w, depth=d,
                               confidence='Resolved', source_url=url, raw_text=raw)
    if any([h, w, d]):
        return DimensionResult(height=h, width=w, depth=d,
                               confidence='Needs Review', source_url=url, raw_text=raw,
                               reason=f'Partial: H={h} W={w} D={d}')
    return None


# ---------------------------------------------------------------------------
# Public adapter class
# ---------------------------------------------------------------------------


class HaierAdapter(BaseAdapter):
    brand_name = 'Haier'

    def fetch_dimensions(self, product_url: str) -> DimensionResult:
        try:
            html, err = fetch_url(product_url)
            if html is None:
                return DimensionResult(confidence='Not Found', source_url=product_url,
                                       reason=err or 'fetch failed')
            soup = BeautifulSoup(html, 'lxml')
            for strategy in (_from_dim_table, _from_free_text):
                result = strategy(soup, product_url)
                if result is not None:
                    return _apply_plausibility(result)
            return DimensionResult(confidence='Not Found', source_url=product_url,
                                   reason='No dimension data found on page')
        except Exception as exc:
            return DimensionResult(confidence='Not Found', source_url=product_url,
                                   reason=f'Unexpected error: {type(exc).__name__}',
                                   raw_text=str(exc))
