"""Beko NZ dimension adapter.

Platform: AEM-based site (www.beko.com/nz-en/).

CRITICAL — DO NOT extract from div.MainSpecs__items / span.DimensionsItem__number.
That widget shows JavaScript-animated placeholder zeros ("0 Height", "0 Width",
"0 Depth") in static HTML.  Naively reading it would produce height=0/width=0/depth=0,
which looks like a resolved result but is entirely fabricated.

Real dimension source: "Tech Specs → Dimensions & Weight" section.
  Row container: div.PropertyTable__row
  Label:  div.att_name label   (text content)
  Value:  div.att_value span[role="textbox"]   (text content, strip whitespace)
  Units: centimetres — convert to mm (× 10). Handles decimal cm, e.g. 59.8 cm → 598 mm.
  Exclusions: Packaged * rows (plus standard list).

Image: no og:image — JSON-LD Product schema (`image` field) in <head>.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from .base import (BaseAdapter, DimensionResult, fetch_url,
                   ImageResult, prepare_image, normalise_image_url)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PLAUSIBLE_MM = (200, 2500)

_EXCLUDE_RE = re.compile(
    r'\b(packaged|packed|packing|packaging|cavity|cutout|cut[\s\-]out'
    r'|shipping|gross|commercial)\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _spec_pairs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Extract (label, value) from all PropertyTable__row divs."""
    pairs: list[tuple[str, str]] = []
    for row in soup.find_all('div', class_='PropertyTable__row'):
        lbl = row.select_one('div.att_name label')
        val = row.select_one('div.att_value span[role="textbox"]')
        if lbl and val:
            label = lbl.get_text(strip=True)
            value = val.get_text(strip=True)
            if label and value:
                pairs.append((label, value))
    return pairs


def _clean_value(raw: str) -> Optional[str]:
    """Convert a dimension string to a plain mm integer string.

    '85 cm'    → '850'
    '59.8 cm'  → '598'   (decimal cm: round(float * 10))
    '600 mm'   → '600'
    Returns None for non-numeric or unrecognised formats (e.g. '49.3 kg').
    """
    raw = raw.strip()
    # cm (integer or decimal): "85 cm", "59.8 cm"
    m = re.match(r'^(\d+(?:\.\d+)?)\s*cm\s*$', raw, re.IGNORECASE)
    if m:
        return str(round(float(m.group(1)) * 10))
    # mm
    m = re.match(r'^(\d+)\s*mm\s*$', raw, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _from_dims(pairs: list[tuple[str, str]], url: str) -> Optional[DimensionResult]:
    """Extract H/W/D from PropertyTable rows; skip packaged and other excluded rows."""
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


class BekoAdapter(BaseAdapter):
    brand_name = 'Beko'

    def get_image_url(self, html: str, page_url: str) -> Optional[str]:
        """Beko: no og:image — extract from JSON-LD Product schema `image` field."""
        soup = BeautifulSoup(html, 'lxml')
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string or '')
                if data.get('@type') == 'Product' and 'image' in data:
                    return normalise_image_url(data['image'], page_url)
            except (json.JSONDecodeError, AttributeError):
                continue
        return None

    def fetch_dimensions(self, product_url: str) -> DimensionResult:
        try:
            html, err = fetch_url(product_url)
            if html is None:
                return DimensionResult(confidence='Not Found', source_url=product_url,
                                       reason=err or 'fetch failed')
            soup = BeautifulSoup(html, 'lxml')
            pairs = _spec_pairs(soup)
            result = _from_dims(pairs, product_url)
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
