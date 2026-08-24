"""Miele NZ dimension adapter.

Platform: Intershop (shop.miele.co.nz).

Dimension strategies (in priority order):
  1. Combined label — `dt.ish-ca-type` whose text contains "dimension" and has an
     explicit axis-order parenthetical e.g. "(W x H x D)" or "(H x W x D)".
     Sibling `dd.ish-ca-value` holds the matching value e.g. "520 x 305 x 422".
     Axis order is read from the label, not hardcoded.
     Covers: microwaves ("Appliance dimensions (W x H x D) in mm"),
             ovens ("Appliance dimensions (W x H x D) in mm"),
             cooktops/washers/dryers ("Dimensions (H x W x D) in mm").
  2. Individual axis labels — `dt.ish-ca-type` containing "appliance" or "dimension"
     AND an axis keyword (height/width/depth); each gives one dimension.
     Covers: dishwashers ("Appliance height/width/depth in mm").

Excluded by both strategies: niche, cutout, cut-out, external, internal, door-open.

Image: no og:image on Miele pages. First `<img src="media.miele.com/images/...">` that
is not a thumbnail (d=110) and whose alt text ends with "product photo" (no
view-descriptor suffix).
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
    r'\b(niche|cutout|cut[\s\-]out|external|internal|door\s+open)\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clean_value(raw: str) -> Optional[str]:
    """Parse 'N' or 'N mm' to a plain mm integer string. Returns None otherwise."""
    raw = raw.strip()
    m = re.match(r'^(\d+)\s*(mm)?\s*$', raw, re.IGNORECASE)
    if not m:
        return None
    return m.group(1)


def _parse_axis_order(label: str) -> Optional[list[str]]:
    """Extract ['W', 'H', 'D'] (or any permutation) from '(W x H x D)' in label."""
    m = re.search(r'\(([WHD])\s*x\s*([WHD])\s*x\s*([WHD])\)', label, re.IGNORECASE)
    if not m:
        return None
    return [m.group(1).upper(), m.group(2).upper(), m.group(3).upper()]


def _from_combined_dim(soup: BeautifulSoup, url: str) -> Optional[DimensionResult]:
    """Strategy 1: combined 'Dimensions (A x B x C)' label with axis-order parsing."""
    for dt in soup.find_all('dt', class_='ish-ca-type'):
        label = dt.get_text(strip=True)
        if 'dimension' not in label.lower():
            continue
        if _EXCLUDE_RE.search(label):
            continue
        axes = _parse_axis_order(label)
        if axes is None:
            continue
        dd = dt.find_next_sibling('dd', class_='ish-ca-value')
        if not dd:
            continue
        value_text = dd.get_text(strip=True)
        parts = re.split(r'\s*x\s*', value_text, flags=re.IGNORECASE)
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
                             url, f'{label}: {value_text}')
    return None


def _from_individual_dims(soup: BeautifulSoup, url: str) -> Optional[DimensionResult]:
    """Strategy 2: individual axis labels — 'Appliance height/width/depth in mm' etc."""
    h = w = d = None
    raw_parts: list[str] = []
    for dt in soup.find_all('dt', class_='ish-ca-type'):
        label = dt.get_text(strip=True)
        label_lower = label.lower()
        if _EXCLUDE_RE.search(label):
            continue
        # Only consider labels relating to the appliance overall dimensions
        if 'appliance' not in label_lower and 'dimension' not in label_lower:
            continue
        dd = dt.find_next_sibling('dd', class_='ish-ca-value')
        if not dd:
            continue
        value = dd.get_text(strip=True)
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


class MieleAdapter(BaseAdapter):
    brand_name = 'Miele'

    def get_image_url(self, html: str, page_url: str) -> Optional[str]:
        """Miele: no og:image. First gallery img whose alt ends with 'product photo'."""
        soup = BeautifulSoup(html, 'lxml')
        for img in soup.find_all('img'):
            src = (img.get('src') or '').strip()
            if 'media.miele.com/images' not in src:
                continue
            if 'd=110' in src:  # skip thumbnails
                continue
            alt = (img.get('alt') or '').strip()
            if re.search(r'\bproduct photo\s*$', alt, re.IGNORECASE):
                return src
        return None

    def fetch_dimensions(self, product_url: str) -> DimensionResult:
        try:
            html, err = fetch_url(product_url)
            if html is None:
                return DimensionResult(confidence='Not Found', source_url=product_url,
                                       reason=err or 'fetch failed')
            soup = BeautifulSoup(html, 'lxml')
            result = None
            for strategy in (_from_combined_dim, _from_individual_dims):
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
