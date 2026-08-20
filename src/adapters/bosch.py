"""Bosch NZ dimension adapter.

Strategy: parse the "Technical Overview" quick-facts block rendered in the
plain HTML response. This block is anchored by data-testid="technical-overview-list"
and contains one data-testid="technical-overview-item" per row.

Value format: "NxNxN mm" with H×W×D axis order (confirmed against all
categories; washing machines label it "Dimensions of the product", all other
categories label it "Dimensions (HxWxD)").

Limitation: the full "Specifications" accordion (Size and weight, etc.)
loads client-side only and is not available in a plain fetch. If the
Technical Overview block does not contain a dimension item for a given model
(e.g. fridges / freezers), the adapter returns Not Found. Headless-browser
support is not in scope for this pass.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseAdapter, DimensionResult, fetch_url, extract_and_prepare_image

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PLAUSIBLE_MM = (200, 2500)

# Labels containing any of these words are excluded even if they say "dimension"
_EXCLUDE_RE = re.compile(
    r'\b(packed|packing|packaging|shipping|gross|cutout|cut[\s\-]out|cavity|clearance)\b',
    re.IGNORECASE,
)

# Matches "845x598x590 mm", "845x598x590mm", "845 x 598 x 590 mm"
# Axis order is H × W × D for all Bosch NZ Technical Overview entries.
_DIM_RE = re.compile(
    r'^(\d+)\s*[xX]\s*(\d+)\s*[xX]\s*(\d+)\s*(mm|cm)?\s*$',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_hwxd(raw: str) -> Optional[tuple[str, str, str]]:
    """Parse 'NxNxN mm' into (height, width, depth) plain-number strings.

    Returns None if the value does not match the expected format.
    Handles optional spaces around 'x', trailing mm/cm unit.
    """
    m = _DIM_RE.match(raw.strip())
    if not m:
        return None
    h_raw, w_raw, d_raw, unit = m.groups()
    if unit and unit.lower() == 'cm':
        return (str(int(h_raw) * 10), str(int(w_raw) * 10), str(int(d_raw) * 10))
    return (h_raw, w_raw, d_raw)


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


def _from_technical_overview(soup: BeautifulSoup, url: str) -> Optional[DimensionResult]:
    """Parse the Technical Overview quick-facts block via data-testid anchors."""
    overview = soup.find(attrs={'data-testid': 'technical-overview-list'})
    if overview is None:
        return None

    for item in overview.find_all(attrs={'data-testid': 'technical-overview-item'}):
        texts = [t.strip() for t in item.strings if t.strip()]
        if not texts:
            continue
        label = texts[0]
        value = ' '.join(texts[1:])

        if 'dimension' not in label.lower():
            continue
        if _EXCLUDE_RE.search(label):
            continue

        parsed = _parse_hwxd(value)
        if parsed is None:
            continue

        h, w, d = parsed
        raw_text = f'{label}: {value}'
        return DimensionResult(
            height=h, width=w, depth=d,
            confidence='Resolved',
            source_url=url,
            raw_text=raw_text,
        )

    return None


# ---------------------------------------------------------------------------
# Public adapter class
# ---------------------------------------------------------------------------


class BoschAdapter(BaseAdapter):
    brand_name = 'Bosch'

    def fetch_dimensions(self, product_url: str) -> DimensionResult:
        try:
            html, err = fetch_url(product_url)
            if html is None:
                return DimensionResult(confidence='Not Found', source_url=product_url,
                                       reason=err or 'fetch failed')
            soup = BeautifulSoup(html, 'lxml')
            result = _from_technical_overview(soup, product_url)
            if result is not None:
                result = _apply_plausibility(result)
            else:
                result = DimensionResult(
                    confidence='Not Found',
                    source_url=product_url,
                    reason='No dimension data in Technical Overview (JS-rendered accordion not accessible)',
                )
            img = extract_and_prepare_image(html, product_url)
            result.image_bytes  = img.image_bytes
            result.image_status = img.status
            return result
        except Exception as exc:
            return DimensionResult(confidence='Not Found', source_url=product_url,
                                   reason=f'Unexpected error: {type(exc).__name__}',
                                   raw_text=str(exc))
