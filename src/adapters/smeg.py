"""Smeg NZ dimension adapter.

Strategy (in priority order):
  1. Combined label — `span.detail__label` containing "dimension" with an axis-order
     tag such as "HxWxD"; sibling `span.detail__txt` holds the value e.g.
     "848x598x600 mm" (zero-space x-separated).  Axis order is read from the label.
  2. Individual axis labels — `span.detail__label` containing height/width/depth
     keywords: "Product Height (mm)", "Width (mm)", "Depth (mm)".

Smeg product pages have three tiers of dimension-like rows:
  Tier 1 (Commercial height/width) — rounded marketing sizes, excluded by "commercial".
  Tier 2 (Logistic Information section) — precise figures, targeted by both strategies.
  Tier 3 (packed/packaged rows) — packaging dims, excluded by "packed"/"packaged".

Exclusion list (broader than earlier brands to catch Smeg-specific wording):
  commercial, packed, packaged, packing, packaging, cavity, cutout, shipping, gross.

Image: og:image is present and clean — inherits BaseAdapter.
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
    r'\b(commercial|packed|packaged|packing|packaging|cavity|cutout|cut[\s\-]out'
    r'|shipping|gross)\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _spec_pairs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Extract (label, value) from all div.detail span pairs."""
    pairs: list[tuple[str, str]] = []
    for div in soup.find_all('div', class_='detail'):
        lbl = div.find('span', class_='detail__label')
        val = div.find('span', class_='detail__txt')
        if lbl and val:
            label = lbl.get_text(strip=True)
            value = val.get_text(strip=True)
            if label and value:
                pairs.append((label, value))
    return pairs


def _parse_axis_order(label: str) -> Optional[list[str]]:
    """Extract ['H', 'W', 'D'] (or any permutation) from an AxBxC tag in the label."""
    m = re.search(r'([WHD])x([WHD])x([WHD])', label, re.IGNORECASE)
    if m:
        return [m.group(1).upper(), m.group(2).upper(), m.group(3).upper()]
    return None


def _clean_value(raw: str) -> Optional[str]:
    """Parse 'N' or 'N mm' to a plain mm integer string."""
    raw = raw.strip()
    m = re.match(r'^(\d+)\s*(mm)?\s*$', raw, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _from_combined_dim(pairs: list[tuple[str, str]], url: str) -> Optional[DimensionResult]:
    """Strategy 1: combined 'Dimensions ... HxWxD' label → split value on x."""
    for label, value in pairs:
        if 'dimension' not in label.lower():
            continue
        if _EXCLUDE_RE.search(label):
            continue
        axes = _parse_axis_order(label)
        if axes is None:
            continue
        # Strip trailing unit then split: "848x598x600 mm" → ["848","598","600"]
        val_clean = re.sub(r'\s*mm\.?\s*$', '', value.strip(), flags=re.IGNORECASE).strip()
        parts = re.split(r'\s*x\s*', val_clean, flags=re.IGNORECASE)
        if len(parts) != 3:
            continue
        dims: dict[str, str] = {}
        valid = True
        for axis, part in zip(axes, parts):
            cleaned = _clean_value(part)
            if cleaned is None:
                valid = False
                break
            dims[axis] = cleaned
        if valid and len(dims) == 3:
            return _assemble(dims.get('H'), dims.get('W'), dims.get('D'),
                             url, f'{label} {value}')
    return None


def _from_individual_dims(pairs: list[tuple[str, str]], url: str) -> Optional[DimensionResult]:
    """Strategy 2: individual 'Product Height', 'Width', 'Depth' labels."""
    h = w = d = None
    raw_parts: list[str] = []
    for label, value in pairs:
        if _EXCLUDE_RE.search(label):
            continue
        label_lower = label.lower()
        cleaned = _clean_value(value)
        if cleaned is None:
            continue
        if 'height' in label_lower and h is None:
            h = cleaned
            raw_parts.append(f'{label} {value}')
        elif 'width' in label_lower and w is None:
            w = cleaned
            raw_parts.append(f'{label} {value}')
        elif 'depth' in label_lower and d is None:
            d = cleaned
            raw_parts.append(f'{label} {value}')
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


class SmegAdapter(BaseAdapter):
    brand_name = 'Smeg'

    def fetch_dimensions(self, product_url: str) -> DimensionResult:
        try:
            html, err = fetch_url(product_url)
            if html is None:
                return DimensionResult(confidence='Not Found', source_url=product_url,
                                       reason=err or 'fetch failed')
            soup = BeautifulSoup(html, 'lxml')
            pairs = _spec_pairs(soup)
            result = None
            for strategy in (_from_combined_dim, _from_individual_dims):
                result = strategy(pairs, product_url)
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
