"""Base adapter interface, shared DimensionResult type, and HTTP helper."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urljoin

import requests

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DimensionResult:
    """
    Outcome of a single dimension lookup attempt.

    confidence values (used verbatim in the output workbook):
      'Resolved'     — height, width, depth are all populated and unambiguous
      'Needs Review' — partial or ambiguous data found; see raw_text
      'Not Found'    — nothing useful located
    """
    height:       Optional[str]   = None
    width:        Optional[str]   = None
    depth:        Optional[str]   = None
    confidence:   str             = 'Not Found'
    source_url:   str             = ''
    raw_text:     str             = ''   # raw spec snippet for audit / debugging
    reason:       str             = ''   # human-readable explanation for non-Resolved outcomes
    image_bytes:  Optional[bytes] = None # JPEG thumbnail bytes, or None
    image_status: str             = 'Not Found'  # 'Embedded' | 'Not Found' | 'Download Failed'


@dataclass
class ImageResult:
    """Outcome of a single image extraction attempt."""
    image_bytes: Optional[bytes] = None
    status:      str             = 'Not Found'  # 'Embedded' | 'Not Found' | 'Download Failed'


# ---------------------------------------------------------------------------
# Shared HTTP helper  (module-level rate-limiter shared across all adapters)
# ---------------------------------------------------------------------------

_LAST_REQ: dict[str, float] = defaultdict(float)
_MIN_DELAY_S = 1.5   # seconds between requests to the same domain
_REQUEST_TIMEOUT_S = 10

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-NZ,en;q=0.9',
}


def fetch_url(url: str, *, binary: bool = False) -> tuple[Optional[bytes | str], str]:
    """
    Politely fetch a URL.

    Returns (content, error_reason).
    content is None when the fetch failed; error_reason describes why.
    binary=True returns raw bytes (for PDFs); False returns decoded text.
    Respects a per-domain minimum delay to avoid hammering sites.
    """
    domain = urlparse(url).netloc

    elapsed = time.monotonic() - _LAST_REQ[domain]
    if elapsed < _MIN_DELAY_S:
        time.sleep(_MIN_DELAY_S - elapsed)
    _LAST_REQ[domain] = time.monotonic()

    try:
        resp = requests.get(
            url,
            headers=_HEADERS,
            timeout=_REQUEST_TIMEOUT_S,
            allow_redirects=True,
        )
    except requests.Timeout:
        return None, 'timeout'
    except requests.RequestException as exc:
        return None, f'request error: {exc}'

    if resp.status_code in (403, 429):
        return None, f'access refused (HTTP {resp.status_code})'
    if not resp.ok:
        return None, f'HTTP {resp.status_code}'

    if binary:
        return resp.content, ''
    return resp.text, ''


def extract_and_prepare_image(html: str, base_url: str) -> ImageResult:
    """
    Parse og:image from already-fetched page HTML, download and resize to a
    JPEG thumbnail (~180px on the longer edge).

    Never raises — returns ImageResult with status='Not Found' or
    'Download Failed' on any failure.
    Reuses the shared fetch_url rate-limiter for the image download.
    """
    try:
        from bs4 import BeautifulSoup
        from io import BytesIO
        from PIL import Image as PILImage

        soup = BeautifulSoup(html, 'lxml')
        og_tag = soup.find('meta', attrs={'property': 'og:image'})
        if og_tag is None:
            return ImageResult(status='Not Found')

        img_url = (og_tag.get('content') or '').strip()
        if not img_url:
            return ImageResult(status='Not Found')

        # Normalise protocol-relative and root-relative URLs
        if img_url.startswith('//'):
            img_url = 'https:' + img_url
        elif not img_url.startswith('http'):
            img_url = urljoin(base_url, img_url)

        img_data, _err = fetch_url(img_url, binary=True)
        if img_data is None:
            return ImageResult(status='Download Failed')

        img = PILImage.open(BytesIO(img_data)).convert('RGB')
        max_px = 180
        w, h = img.size
        ratio = min(max_px / w, max_px / h)
        if ratio < 1.0:
            img = img.resize((int(w * ratio), int(h * ratio)), PILImage.LANCZOS)

        buf = BytesIO()
        img.save(buf, format='JPEG', quality=85)
        return ImageResult(image_bytes=buf.getvalue(), status='Embedded')

    except Exception:
        return ImageResult(status='Download Failed')


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class BaseAdapter:
    """
    Abstract base for brand-specific dimension adapters.

    Subclasses must override `fetch_dimensions`.
    Stub adapters can inherit from `StubAdapter` below instead.
    """
    brand_name: str = ''  # informational; set in subclass

    def fetch_dimensions(self, product_url: str) -> DimensionResult:
        """
        Look up Height / Width / Depth for the product at product_url.

        Must never raise — always returns a DimensionResult.
        """
        raise NotImplementedError


class StubAdapter(BaseAdapter):
    """Placeholder for brands whose adapter has not been implemented yet."""

    def fetch_dimensions(self, product_url: str) -> DimensionResult:
        return DimensionResult(
            confidence='Not Found',
            source_url=product_url,
            reason='Adapter not yet implemented',
        )
