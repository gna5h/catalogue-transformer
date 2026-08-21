"""LG NZ product page adapter for dimension extraction.

Strategy (in order):
  1. Parse __NEXT_DATA__ JSON embedded by Next.js SSR
  2. Parse JSON-LD structured data
  3. Parse HTML spec tables (dl/dt/dd, table rows, class-based patterns)
  4. Follow a linked spec-sheet PDF and extract text

Convention flag:
  DIMENSION_CONVENTION = "overall"  — use net/overall unit dimensions
  DIMENSION_CONVENTION = "cavity"   — use cutout/installation dimensions
  Change here if eProcess requirements are confirmed to need cavity dims.

Module isolation:
  All module-level functions (_detect_unit, _convert_to_mm, _parse_axis_order,
  _parse_numbers, _is_cavity_label, _is_overall_label, _should_use_label,
  _deep_search_specs, _specs_from_next_data, _specs_from_jsonld, _specs_from_html,
  _specs_from_text, _build_result_from_specs) are private helpers for LGAdapter
  only. The underscore prefix is the Python convention for internal use; they are
  NOT part of any shared adapter API and must not be imported by other modules.
"""

from __future__ import annotations

import io
import json
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseAdapter, DimensionResult, fetch_url, ImageResult, prepare_image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DIMENSION_CONVENTION = 'overall'   # 'overall' | 'cavity'

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Axis triplet in a label: W x H x D, WxHxD, W*H*D etc.
_AXIS_TRIPLET_RE = re.compile(
    r'([WHDwhd])\s*[×xX*]\s*([WHDwhd])\s*[×xX*]\s*([WHDwhd])'
)

# Numeric values including ranges: "595", "638-1000"
_NUMBERS_RE = re.compile(r'\d+(?:\s*[-–]\s*\d+)?')

# A label is dimension-related (matches both singular and plural: "Dimension", "Dimensions")
_DIM_KEYWORD_RE = re.compile(
    r'\b(net\s+dimensions?|gross\s+dimensions?|overall\s+dimensions?|'
    r'product\s+dimensions?|unit\s+dimensions?|dimensions?|width|height|depth)\b',
    re.IGNORECASE,
)

# A label refers to cavity / cutout / packaging / adjustable features, or a
# clearance measurement (door-open swing, back-cover-to-door distance) — not
# the product's own footprint.
_CAVITY_RE = re.compile(
    r'\b(cutout|cut[\s-]out|cavity|recess|installation|opening|'
    r'pack(?:ing|aging)|shipping|gross|adjustable|'
    r'clearance|door\s+open|back\s+cover)\b',
    re.IGNORECASE,
)

# A label refers to overall / net size
_OVERALL_RE = re.compile(
    r'\b(net|overall|total|product|unit|external|outer)\b',
    re.IGNORECASE,
)

# Preferred dimension labels — promoted to front of candidate list
_PREFERRED_LABEL_RE = re.compile(
    r'\b(product\s+dimensions?|net\s+dimensions?)\b',
    re.IGNORECASE,
)

# Unit tag in a label
_UNIT_RE = re.compile(r'\((mm|cm|m)\)', re.IGNORECASE)

# Single-axis keywords (full words before abbreviations so 'depth' is matched before 'h')
_SINGLE_AXIS = {
    'width':  'W',
    'height': 'H',
    'depth':  'D',
    'w':      'W',
    'h':      'H',
    'd':      'D',
}

# Plausibility bounds for any single axis of a major household appliance (mm)
_PLAUSIBLE_MM = (200, 2500)


# ---------------------------------------------------------------------------
# Dimension parsing helpers
# ---------------------------------------------------------------------------

def _detect_unit(label: str, value: str) -> str:
    m = _UNIT_RE.search(label) or _UNIT_RE.search(value)
    return m.group(1).lower() if m else 'mm'


def _convert_to_mm(raw: str, unit: str) -> str:
    """Convert a raw number string to mm if the source unit is cm."""
    if unit == 'cm':
        try:
            # Handle ranges: "85-90 cm" → "850-900 mm"
            parts = re.split(r'\s*[-–]\s*', raw)
            converted = [str(int(float(p) * 10)) for p in parts]
            return '-'.join(converted)
        except (ValueError, TypeError):
            return raw
    return raw.strip()


def _parse_axis_order(label: str) -> list[str]:
    """Return ['W','H','D'] in the order found in the label, or []."""
    m = _AXIS_TRIPLET_RE.search(label)
    if m:
        return [a.upper() for a in m.groups()]
    return []


def _parse_numbers(value: str) -> list[str]:
    # Normalise zero-space dimension triplets like "600x850x550": replace x/×
    # between digits with a space so each value is extracted cleanly.
    normalised = re.sub(r'(?<=\d)[x×X](?=\d)', ' ', value.replace(',', ''))
    return _NUMBERS_RE.findall(normalised)


def _is_cavity_label(label: str) -> bool:
    return bool(_CAVITY_RE.search(label))


def _is_overall_label(label: str) -> bool:
    return bool(_OVERALL_RE.search(label))


def _should_use_label(label: str) -> bool:
    """Decide whether to use this dimension entry given DIMENSION_CONVENTION."""
    is_cavity  = _is_cavity_label(label)
    is_overall = _is_overall_label(label)
    if DIMENSION_CONVENTION == 'overall':
        # Skip any cavity/clearance label even when it also contains an "overall"
        # keyword (e.g. "Product Depth with door open 90˚" has "product" but is
        # not the product's own footprint — it is a door-swing clearance value).
        if is_cavity:
            return False
    elif DIMENSION_CONVENTION == 'cavity':
        if is_overall and not is_cavity:
            return False
    return True


# ---------------------------------------------------------------------------
# Spec extraction helpers
# ---------------------------------------------------------------------------

def _deep_search_specs(obj, results: list) -> None:
    """Recursively collect (label, value) spec pairs from parsed JSON."""
    if isinstance(obj, dict):
        key_l   = obj.get('key')   or obj.get('label') or obj.get('name')
        value_l = obj.get('value') or obj.get('data')
        if key_l and value_l and isinstance(key_l, str) and isinstance(value_l, str):
            results.append((key_l, value_l))
        for v in obj.values():
            _deep_search_specs(v, results)
    elif isinstance(obj, list):
        for item in obj:
            _deep_search_specs(item, results)


def _specs_from_next_data(soup: BeautifulSoup) -> list[tuple[str, str]]:
    tag = soup.find('script', id='__NEXT_DATA__')
    if not tag:
        return []
    try:
        data = json.loads(tag.string or '')
    except (json.JSONDecodeError, TypeError):
        return []
    results: list[tuple[str, str]] = []
    _deep_search_specs(data, results)
    return results


def _specs_from_jsonld(soup: BeautifulSoup) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(tag.string or '')
        except (json.JSONDecodeError, TypeError):
            continue
        _deep_search_specs(data, results)
    return results


def _specs_from_html(soup: BeautifulSoup) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []

    # dl / dt+dd pairs
    for dl in soup.find_all('dl'):
        dts = dl.find_all('dt')
        dds = dl.find_all('dd')
        for dt, dd in zip(dts, dds):
            results.append((dt.get_text(' ', strip=True), dd.get_text(' ', strip=True)))

    # table rows (first two cells)
    for table in soup.find_all('table'):
        for tr in table.find_all('tr'):
            cells = tr.find_all(['td', 'th'])
            if len(cells) >= 2:
                results.append((
                    cells[0].get_text(' ', strip=True),
                    cells[1].get_text(' ', strip=True),
                ))

    # Class-pattern divs (LG sometimes uses .spec-row, .specification-item, etc.)
    for item in soup.find_all(class_=re.compile(r'spec', re.I)):
        label_el = item.find(class_=re.compile(r'label|name|key|title', re.I))
        value_el = item.find(class_=re.compile(r'value|data|content', re.I))
        if label_el and value_el and label_el != value_el:
            results.append((
                label_el.get_text(' ', strip=True),
                value_el.get_text(' ', strip=True),
            ))

    # LG AEM CMS component: c-text-contents spec rows
    # Structure: div.c-text-contents > strong.cmp-title__text (label) + div.cmp-text (value)
    for item in soup.find_all(class_=re.compile(r'c-text-contents', re.I)):
        label_el = item.find(class_='cmp-title__text')
        value_el = item.find(class_='cmp-text')
        if label_el and value_el and label_el != value_el:
            results.append((
                label_el.get_text(' ', strip=True),
                value_el.get_text(' ', strip=True),
            ))

    # LG AEM CMS component: c-compare-selling spec rows (product comparison pages)
    # Structure: li.c-compare-selling__item >
    #   div.c-compare-selling__spec-name (label) + div.c-compare-selling__spec-desc (value)
    # Extract labels that contain an axis triplet (WxHxD) OR a dimension keyword
    # (height, width, depth) — the latter covers fridge single-axis labels.
    # Non-dimension feature labels (adjustable, packing, etc.) are handled downstream
    # by _CAVITY_RE / _should_use_label.
    for item in soup.find_all(class_='c-compare-selling__item'):
        label_el = item.find(class_='c-compare-selling__spec-name')
        value_el = item.find(class_='c-compare-selling__spec-desc')
        if label_el and value_el and label_el != value_el:
            label = label_el.get_text(' ', strip=True)
            if _AXIS_TRIPLET_RE.search(label) or _DIM_KEYWORD_RE.search(label):
                results.append((label, value_el.get_text(' ', strip=True)))

    return results


def _specs_from_text(text: str) -> list[tuple[str, str]]:
    """Parse plain text (from PDF) into (label, value) pairs."""
    results = []
    for line in text.splitlines():
        # "Label: Value" or "Label  Value" (tab-separated)
        m = re.match(r'^(.{3,60}?)\s*[:\t]\s*(.+)$', line.strip())
        if m:
            results.append((m.group(1).strip(), m.group(2).strip()))
    return results


# ---------------------------------------------------------------------------
# Depth-variant helpers
# ---------------------------------------------------------------------------

def _depth_variant_score(label: str) -> int:
    """Priority for depth-variant labels. Higher score = more preferred."""
    lo = label.lower()
    if 'handle' in lo and 'without' not in lo:
        return 3   # "Depth - With Door & Handle"
    if 'handle' in lo:
        return 2   # "Depth - Without Handle"
    if 'without' in lo or 'door' in lo:
        return 1   # "Depth - Without Door"
    return 0       # generic depth label


def _resolve_depth_variants(
    dim_specs: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """
    When multiple single-axis depth labels are present (e.g. "Depth - Without Door"
    and "Depth - With Door & Handle"), keep only the highest-priority one.
    Triplet-format labels (WxHxD) are unaffected.
    """
    depth_singles = [
        (i, label, value) for i, (label, value) in enumerate(dim_specs)
        if re.search(r'\bdepth\b', label, re.IGNORECASE)
        and not _AXIS_TRIPLET_RE.search(label)
    ]
    if len(depth_singles) <= 1:
        return dim_specs
    best_idx = max(depth_singles, key=lambda t: _depth_variant_score(t[1]))[0]
    remove = {i for i, _, _ in depth_singles if i != best_idx}
    return [(lbl, val) for i, (lbl, val) in enumerate(dim_specs) if i not in remove]


# ---------------------------------------------------------------------------
# Plausibility guard
# ---------------------------------------------------------------------------

def _apply_plausibility(result: DimensionResult) -> DimensionResult:
    """
    Downgrade a Resolved result to Needs Review if any dimension is outside
    the plausible range for major household appliances (_PLAUSIBLE_MM).
    Non-Resolved results are returned unchanged.
    """
    if result.confidence != 'Resolved':
        return result
    lo, hi = _PLAUSIBLE_MM
    bad = []
    for name, val in [('Height', result.height), ('Width', result.width), ('Depth', result.depth)]:
        if val is None:
            continue
        try:
            v = float(val)
            if not (lo <= v <= hi):
                bad.append(f'{name}={val}')
        except (TypeError, ValueError):
            pass
    if not bad:
        return result
    return DimensionResult(
        height=result.height,
        width=result.width,
        depth=result.depth,
        confidence='Needs Review',
        source_url=result.source_url,
        raw_text=result.raw_text,
        reason=f'Implausible value(s): {", ".join(bad)} (expected {lo}–{hi}mm)',
    )


# ---------------------------------------------------------------------------
# Core dimension parser
# ---------------------------------------------------------------------------

def _build_result_from_specs(
    dim_specs: list[tuple[str, str]],
    source_url: str,
) -> DimensionResult:
    """
    Try to assemble H, W, D from filtered (label, value) dimension pairs.
    Returns a DimensionResult with the appropriate confidence level.
    """
    # Promote preferred labels (Product/Net Dimensions) to the front so that
    # "first write wins" fills H/W/D from the most authoritative source first.
    dim_specs = sorted(
        dim_specs,
        key=lambda kv: 0 if _PREFERRED_LABEL_RE.search(kv[0]) else 1,
    )
    # When multiple depth-variant labels exist, keep only the highest-priority one.
    dim_specs = _resolve_depth_variants(dim_specs)

    h = w = d = None
    raw_parts: list[str] = []
    skipped_cavity: list[str] = []
    conflict_detected = False

    for label, value in dim_specs:
        raw_parts.append(f'{label}: {value}')

        if not _should_use_label(label):
            if _is_cavity_label(label):
                skipped_cavity.append(f'{label}: {value}')
            continue

        unit = _detect_unit(label, value)
        axes = _parse_axis_order(label)

        if axes:
            # Combined W×H×D style
            numbers = _parse_numbers(value)
            if len(numbers) >= 3:
                mapping = dict(zip(axes, [_convert_to_mm(n, unit) for n in numbers[:3]]))
                # Detect conflict: already have values that differ
                if any([
                    h and mapping.get('H') and mapping['H'] != h,
                    w and mapping.get('W') and mapping['W'] != w,
                    d and mapping.get('D') and mapping['D'] != d,
                ]):
                    conflict_detected = True
                h = h or mapping.get('H')
                w = w or mapping.get('W')
                d = d or mapping.get('D')
        else:
            # Try single-axis label
            lo = label.lower()
            numbers = _parse_numbers(value)
            if not numbers:
                continue
            val_mm = _convert_to_mm(numbers[0], unit)
            for word, axis in _SINGLE_AXIS.items():
                if re.search(r'\b' + re.escape(word) + r'\b', lo):
                    if axis == 'H':
                        conflict_detected = conflict_detected or (h and h != val_mm)
                        h = h or val_mm
                    elif axis == 'W':
                        conflict_detected = conflict_detected or (w and w != val_mm)
                        w = w or val_mm
                    elif axis == 'D':
                        conflict_detected = conflict_detected or (d and d != val_mm)
                        d = d or val_mm
                    break

    raw_text = '\n'.join(raw_parts[:12])
    if skipped_cavity:
        raw_text += '\n[Cavity dims skipped]: ' + '; '.join(skipped_cavity[:4])

    if conflict_detected:
        return DimensionResult(
            height=h, width=w, depth=d,
            confidence='Needs Review',
            source_url=source_url,
            raw_text=raw_text,
            reason='Conflicting dimension values found on page',
        )

    if h and w and d:
        return DimensionResult(
            height=h, width=w, depth=d,
            confidence='Resolved',
            source_url=source_url,
            raw_text=raw_text,
        )

    if any([h, w, d]):
        return DimensionResult(
            height=h, width=w, depth=d,
            confidence='Needs Review',
            source_url=source_url,
            raw_text=raw_text,
            reason=f'Partial dimensions found: H={h} W={w} D={d}',
        )

    return DimensionResult(
        confidence='Not Found',
        source_url=source_url,
        raw_text=raw_text,
    )


# ---------------------------------------------------------------------------
# LG Adapter
# ---------------------------------------------------------------------------

class LGAdapter(BaseAdapter):
    brand_name = 'LG'

    # ----------------------------------------------------------------
    def fetch_dimensions(self, product_url: str) -> DimensionResult:
        try:
            html, err = fetch_url(product_url)
            if html is None:
                return DimensionResult(
                    confidence='Not Found',
                    source_url=product_url,
                    reason=err or 'fetch failed',
                )
            soup = BeautifulSoup(html, 'lxml')
            result = self._find_dimensions(soup, product_url)
            img_url = self.get_image_url(html, product_url)
            img = prepare_image(img_url) if img_url else ImageResult(status='Not Found')
            result.image_bytes  = img.image_bytes
            result.image_status = img.status
            return result
        except Exception as exc:
            return DimensionResult(
                confidence='Not Found',
                source_url=product_url,
                reason=f'Unexpected error: {type(exc).__name__}',
                raw_text=str(exc),
            )

    # ----------------------------------------------------------------
    def _find_dimensions(self, soup: BeautifulSoup, product_url: str) -> DimensionResult:
        """Extract H/W/D from the already-parsed soup. Never raises."""
        # Collect all (label, value) spec pairs from every source on the page
        all_specs: list[tuple[str, str]] = []
        all_specs.extend(_specs_from_next_data(soup))
        all_specs.extend(_specs_from_jsonld(soup))
        all_specs.extend(_specs_from_html(soup))

        # Filter to dimension-relevant pairs
        dim_specs = [
            (k, v) for k, v in all_specs
            if _DIM_KEYWORD_RE.search(k) or _AXIS_TRIPLET_RE.search(k)
        ]

        if dim_specs:
            result = _build_result_from_specs(dim_specs, product_url)
            result = _apply_plausibility(result)
            if result.confidence == 'Resolved':
                return result
            # Partial/ambiguous — try PDF fallback before giving up
            partial = result
        else:
            partial = None

        # PDF fallback
        pdf_result = self._try_pdf(soup, product_url)
        if pdf_result and pdf_result.confidence in ('Resolved', 'Needs Review'):
            return _apply_plausibility(pdf_result)

        if partial:
            return partial   # Return whatever we found on the page

        # Nothing useful on page or in PDF; check if dim section exists at all
        if dim_specs:
            raw = '\n'.join(f'{k}: {v}' for k, v in dim_specs[:8])
            return DimensionResult(
                confidence='Needs Review',
                source_url=product_url,
                raw_text=raw,
                reason='Dimension data present but could not be parsed',
            )

        return DimensionResult(
            confidence='Not Found',
            source_url=product_url,
            reason='No dimension data found on page or linked PDFs',
        )

    # ----------------------------------------------------------------
    def _try_pdf(self, soup: BeautifulSoup, base_url: str) -> Optional[DimensionResult]:
        pdf_url = self._find_spec_pdf(soup, base_url)
        if not pdf_url:
            return None

        try:
            import pdfplumber
        except ImportError:
            return None

        content, err = fetch_url(pdf_url, binary=True)
        if content is None:
            return None

        try:
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                text = '\n'.join(page.extract_text() or '' for page in pdf.pages[:8])
        except Exception:
            return None

        specs = _specs_from_text(text)
        dim_specs = [
            (k, v) for k, v in specs
            if _DIM_KEYWORD_RE.search(k) or _AXIS_TRIPLET_RE.search(k)
        ]
        if not dim_specs:
            return None

        result = _build_result_from_specs(dim_specs, pdf_url)
        return result

    # ----------------------------------------------------------------
    @staticmethod
    def _find_spec_pdf(soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Return the URL of a spec-sheet or product-guide PDF, if any."""
        pdf_keywords = re.compile(r'spec|product.guide|data.sheet', re.I)
        for a in soup.find_all('a', href=True):
            href = str(a['href'])
            if '.pdf' not in href.lower():
                continue
            text = a.get_text(strip=True)
            if pdf_keywords.search(text) or pdf_keywords.search(href):
                return urljoin(base_url, href)
        # Some LG pages link PDFs via data attributes
        for el in soup.find_all(attrs={'data-pdf': True}):
            return urljoin(base_url, str(el['data-pdf']))
        return None
