"""Electrolux NZ dimension adapter.

Strategy (in priority order):
  1. Dimension card — locate `h3.title` "Dimension" inside `div.content`; parse
     `li.content-item` text of the form "595 mm (W)" / "456 mm (H)" / "571 mm (D)".
     Axis suffix letter is authoritative.
  2. Specification table — `li.specification-item` pairs (same structure as
     Westinghouse); axis matched by keyword in label; excluded if the label
     contains cabinet / cutout / door-open / lid-open / hoses / packing / gross.

No height-adjustment field exists on the Electrolux NZ site.

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

_EXCLUDE_RE = re.compile(
    r'\b(cabinet|packing|shipping|gross|cutout|cut[\s\-]out|cavity|clearance'
    r'|lid\s+open|door\s+open|hoses?)\b',
    re.IGNORECASE,
)


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


def _from_dim_card(soup: BeautifulSoup, url: str) -> Optional[DimensionResult]:
    """Strategy 1: Dimension card — li.content-item text like '595 mm (W)'."""
    for h3 in soup.find_all('h3', class_='title'):
        if h3.get_text(strip=True).lower() != 'dimension':
            continue
        parent = h3.parent  # div.content
        ul = parent.find('ul', class_='content-category')
        if not ul:
            continue
        h = w = d = None
        raw_parts: list[str] = []
        for li in ul.find_all('li', class_='content-item'):
            text = li.get_text(strip=True)
            # Matches "595 mm (W)", "456mm(H)", "571 mm (D)"
            m = re.match(r'^(\d+)\s*(mm|cm)?\s*\(([WHD])\)\s*$', text, re.IGNORECASE)
            if not m:
                continue
            n, unit, axis = m.groups()
            val = str(int(n) * 10) if (unit and unit.lower() == 'cm') else n
            raw_parts.append(text)
            axis = axis.upper()
            if axis == 'H' and h is None:
                h = val
            elif axis == 'W' and w is None:
                w = val
            elif axis == 'D' and d is None:
                d = val
        if any([h, w, d]):
            return _assemble(h, w, d, url, ' | '.join(raw_parts))
    return None


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
    """Strategy 2: Specification table — axis keyword matching with exclusions."""
    h = w = d = None
    raw_parts: list[str] = []
    for label, value in _spec_pairs(soup):
        if _EXCLUDE_RE.search(label):
            continue
        label_lower = label.lower()
        cleaned = _clean_value(value)
        if cleaned is None:
            continue
        if 'height' in label_lower and h is None:
            h = cleaned
            raw_parts.append(f'{label}: {value}')
        elif 'width' in label_lower and w is None:
            w = cleaned
            raw_parts.append(f'{label}: {value}')
        elif 'depth' in label_lower and d is None:
            d = cleaned
            raw_parts.append(f'{label}: {value}')
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


class ElectroluxAdapter(BaseAdapter):
    brand_name = 'Electrolux'

    def fetch_dimensions(self, product_url: str) -> DimensionResult:
        try:
            html, err = fetch_url(product_url)
            if html is None:
                return DimensionResult(confidence='Not Found', source_url=product_url,
                                       reason=err or 'fetch failed')
            soup = BeautifulSoup(html, 'lxml')
            result = None
            for strategy in (_from_dim_card, _from_spec_table):
                result = strategy(soup, product_url)
                if result is not None:
                    result = _apply_plausibility(result)
                    break
            if result is None:
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
