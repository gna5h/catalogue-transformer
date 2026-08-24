"""Samsung NZ dimension adapter.

Strategy: scan all <dt>/<dd> pairs across the page for two label formats.

  1. Combined "(WxHxD)" label — detected by "wxhxd" appearing in the label text.
     Axis order is W × H × D for all confirmed Samsung NZ variants:
       "Net Dimension (WxHxD)" — washing machines / dryers
       "Net (WxHxD)"           — cooktops
       "Outside (WxHxD)"       — ovens
  2. Individual axis labels containing "net" plus the axis keyword.
       "Net Width" / "Net Height" / "Net Depth" — dishwashers
       "Net Width(mm)" / "Net Case Height with Hinge(mm)" / etc. — fridges
     First non-excluded match per axis is used (relevant for fridges where
     multiple "Net ... Height ..." variants appear; first is the installed height
     including hinge, which is the value we want).

Excluded labels: gross, package/packing/packaging, shipping, cutout, cavity, clearance.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from .base import (BaseAdapter, DimensionResult, fetch_url, ImageResult, prepare_image,
                   get_og_image_url, normalise_image_url)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PLAUSIBLE_MM = (200, 2500)

_EXCLUDE_RE = re.compile(
    r'\b(gross|package|packing|packaging|shipping|cutout|cut[\s\-]out|cavity|clearance)\b',
    re.IGNORECASE,
)

# Detects combined WxHxD labels (catches all confirmed Samsung NZ variants)
_WXHXD_LABEL_RE = re.compile(r'wxhxd', re.IGNORECASE)

_GENERIC_LOGO_RE = re.compile(r'logo-square-letter', re.IGNORECASE)

# Parses "N x N x N mm" or "N X N X N" combined value in W x H x D order
_WXHXD_VALUE_RE = re.compile(
    r'^(\d+)\s*[xX]\s*(\d+)\s*[xX]\s*(\d+)\s*(mm|cm)?\s*$',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _is_excluded(label: str) -> bool:
    return bool(_EXCLUDE_RE.search(label))


def _clean_value(raw: str) -> Optional[str]:
    """Normalise a single-axis value string ("598 mm", "598", "59 cm") to plain mm."""
    raw = raw.strip()
    m = re.match(r'^(\d+)\s*(mm|cm)?\s*$', raw, re.IGNORECASE)
    if m:
        n, unit = m.groups()
        if unit and unit.lower() == 'cm':
            return str(int(n) * 10)
        return n
    return None


def _parse_wxhxd(value: str) -> Optional[tuple[str, str, str]]:
    """Parse 'W x H x D' into (height, width, depth) plain-mm strings."""
    m = _WXHXD_VALUE_RE.match(value.strip())
    if not m:
        return None
    w_raw, h_raw, d_raw, unit = m.groups()
    if unit and unit.lower() == 'cm':
        return (str(int(h_raw) * 10), str(int(w_raw) * 10), str(int(d_raw) * 10))
    return (h_raw, w_raw, d_raw)


def _spec_pairs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Extract (label, value) pairs from all known Samsung spec HTML structures.

    Structure 1 (current): <p class="pdd32-product-spec__content-item-title"> /
                            <p class="pdd32-product-spec__content-item-desc">
    Structure 2 (legacy):  <dt> / <dd>
    """
    pairs: list[tuple[str, str]] = []

    for title_tag in soup.find_all('p', class_='pdd32-product-spec__content-item-title'):
        label = title_tag.get_text(strip=True)
        desc_tag = title_tag.find_next_sibling('p', class_='pdd32-product-spec__content-item-desc')
        if label and desc_tag:
            value = desc_tag.get_text(strip=True)
            if value:
                pairs.append((label, value))

    for dt_tag in soup.find_all('dt'):
        label = dt_tag.get_text(strip=True)
        dd_tag = dt_tag.find_next_sibling('dd')
        if label and dd_tag:
            value = dd_tag.get_text(strip=True)
            if value:
                pairs.append((label, value))

    return pairs


def _from_spec_table(soup: BeautifulSoup, url: str) -> Optional[DimensionResult]:
    """Scan spec label/value pairs for dimension data using combined and individual formats."""
    h = w = d = None
    raw_parts: list[str] = []

    for label, value in _spec_pairs(soup):
        if _is_excluded(label):
            continue

        label_lower = label.lower()

        # Strategy 1: combined "(WxHxD)" entry
        if _WXHXD_LABEL_RE.search(label_lower):
            parsed = _parse_wxhxd(value)
            if parsed:
                h_val, w_val, d_val = parsed
                raw_parts.append(f'{label}: {value}')
                if h is None:
                    h = h_val
                if w is None:
                    w = w_val
                if d is None:
                    d = d_val
            continue

        # Strategy 2: individual "Net <axis>" entries
        if 'net' not in label_lower:
            continue
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


class SamsungAdapter(BaseAdapter):
    brand_name = 'Samsung'

    def get_image_url(self, html: str, page_url: str) -> Optional[str]:
        """/business/ URLs serve a generic logo as og:image; fall back to consumer URL."""
        if '/business/' in page_url:
            consumer_url = page_url.replace('/business/', '/', 1)
            consumer_html, _err = fetch_url(consumer_url)
            if consumer_html is None:
                return None
            raw = get_og_image_url(consumer_html)
        else:
            raw = get_og_image_url(html)
        if not raw or _GENERIC_LOGO_RE.search(raw):
            return None
        return normalise_image_url(raw, page_url)

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
