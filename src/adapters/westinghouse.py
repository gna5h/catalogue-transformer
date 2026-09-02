"""Westinghouse NZ dimension adapter.

Strategy: scan <li class="specification-item"> elements in specification-category lists.

Labels matched (case-insensitive):
  Primary (label contains 'total' AND axis keyword):
    'Total height (mm)'         / 'Total product height (mm)' → height
    'Total width (mm)'          / 'Total product width (mm)'  → width
    'Total depth (mm)'          / 'Total product depth (mm)'  → depth
  Adjustment (label contains 'height adjustment'):
    When present and non-zero: height = Total height + Height adjustment.
    Raw_text preserves the basis for audit, e.g.
      "Total height 815mm + Height adjustment 50mm = 865mm".

Naturally excluded by not matching 'total + axis':
  Cabinet height/width/depth, Depth door open, Height with lid open,
  Depth with hoses, Cut out width/depth, Height adjustable feet.

Image: og:image is clean on all pages — no override (inherits BaseAdapter).
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseAdapter, DimensionResult, fetch_url, ImageResult, prepare_image

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PLAUSIBLE_MM = (200, 2500)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clean_value(raw: str) -> Optional[str]:
    """Parse 'N mm', 'N cm', or bare 'N' to a plain mm string."""
    raw = raw.strip()
    m = re.match(r'^(\d+)\s*(mm|cm)?\s*$', raw, re.IGNORECASE)
    if not m:
        return None
    n, unit = m.groups()
    if unit and unit.lower() == 'cm':
        return str(int(n) * 10)
    return n


def _spec_pairs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Extract (label, value) pairs from all specification-category list items."""
    pairs: list[tuple[str, str]] = []
    for li in soup.find_all('li', class_='specification-item'):
        lbl_tag = li.find('span', class_='units')
        val_tag = li.find('span', class_='dimension')
        if lbl_tag and val_tag:
            label = lbl_tag.get_text(strip=True)
            value = val_tag.get_text(strip=True)
            if label and value:
                pairs.append((label, value))
    return pairs


def _from_spec_table(soup: BeautifulSoup, url: str) -> Optional[DimensionResult]:
    """Scan spec pairs for dimension data."""
    h = w = d = None
    adj: Optional[str] = None
    raw_parts: list[str] = []

    for label, value in _spec_pairs(soup):
        label_lower = label.lower()

        if 'total' in label_lower:
            if 'height' in label_lower and h is None:
                cleaned = _clean_value(value)
                if cleaned:
                    h = cleaned
                    raw_parts.append(f'{label}: {value}')
            elif 'width' in label_lower and w is None:
                cleaned = _clean_value(value)
                if cleaned:
                    w = cleaned
                    raw_parts.append(f'{label}: {value}')
            elif 'depth' in label_lower and d is None:
                cleaned = _clean_value(value)
                if cleaned:
                    d = cleaned
                    raw_parts.append(f'{label}: {value}')

        elif 'height adjustment' in label_lower and adj is None:
            cleaned = _clean_value(value)
            if cleaned and int(cleaned) > 0:
                adj = cleaned
                raw_parts.append(f'{label}: {value}')

    if h is not None and adj is not None:
        h_adj = str(int(h) + int(adj))
        raw_parts.append(f'Total height {h}mm + Height adjustment {adj}mm = {h_adj}mm')
        h = h_adj

    return _assemble(h, w, d, url, ' | '.join(raw_parts))


def _apply_plausibility(result: DimensionResult) -> DimensionResult:
    """Downgrade to Needs Review if any dimension falls outside 200–2500 mm."""
    lo, hi = _PLAUSIBLE_MM
    issues: list[str] = []
    for axis, val in [('height', result.height), ('width', result.width), ('depth', result.depth)]:
        if val is None:
            continue
        n = int(val)
        if not (lo <= n <= hi):
            issues.append(f'{axis}={val} out of range [{lo},{hi}]')
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
    """Map H/W/D to a DimensionResult, or None if all axes are absent."""
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


class WestinghouseAdapter(BaseAdapter):
    brand_name = 'Westinghouse'

    def fetch_dimensions(self, product_url: str) -> DimensionResult:
        try:
            html, err = fetch_url(product_url)
            if html is None:
                return DimensionResult(confidence='Not Found', source_url=product_url,
                                       reason=err or 'fetch failed')
            soup = BeautifulSoup(html, 'lxml')
            result = _from_spec_table(soup, product_url)
            if result is not None:
                result = _apply_plausibility(result)
            else:
                result = DimensionResult(confidence='Not Found', source_url=product_url,
                                         reason='No dimension data found on page')
            img_url = self.get_image_url(html, product_url)
            img = prepare_image(img_url) if img_url else ImageResult(status='Not Found')
            result.image_bytes  = img.image_bytes
            result.image_status = img.status
            return result
        except Exception as exc:
            return DimensionResult(confidence='Not Found', source_url=product_url,
                                   reason=f'Unexpected error: {type(exc).__name__}',
                                   raw_text=str(exc))
