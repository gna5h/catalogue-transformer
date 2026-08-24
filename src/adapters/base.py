"""Base adapter interface, shared DimensionResult type, and HTTP helper."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urljoin

try:
    from curl_cffi import requests          # TLS/HTTP2 fingerprint impersonation (bypasses Akamai/Cloudflare WAF)
    _CFFI_IMPERSONATE = 'chrome124'
except ImportError:
    import requests                         # type: ignore[no-redef]
    _CFFI_IMPERSONATE = None

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
        get_kwargs: dict = dict(
            headers=_HEADERS,
            timeout=_REQUEST_TIMEOUT_S,
            allow_redirects=True,
        )
        if _CFFI_IMPERSONATE:
            get_kwargs['impersonate'] = _CFFI_IMPERSONATE
        resp = requests.get(url, **get_kwargs)
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


# ---------------------------------------------------------------------------
# Image URL helpers (shared across adapters)
# ---------------------------------------------------------------------------


def is_valid_single_url(value: str) -> bool:
    """Return True if value is a single well-formed URL, not two URLs concatenated.

    Detects the Salesforce Commerce Cloud Demandware template bug where the
    og:image content is a root-relative or absolute path with a second absolute
    URL appended directly:
      /on/demandware.static/.../https://cdn.example.com/img.jpg   (F&P pattern)
      https://site.com/on/demandware.../https://cdn.../img.jpg    (Haier pattern)
    """
    stripped = (value or '').strip()
    if not stripped:
        return False
    count = len(re.findall(r'https?://', stripped))
    if count > 1:
        return False
    # A root-relative path (/foo) that contains a scheme is a concatenation bug
    if count == 1 and not stripped.startswith('http') and not stripped.startswith('//'):
        return False
    return True


def normalise_image_url(raw: str, base_url: str) -> str:
    """Resolve a protocol-relative or root-relative URL to absolute."""
    if raw.startswith('//'):
        return 'https:' + raw
    if not raw.startswith('http'):
        return urljoin(base_url, raw)
    return raw


def get_og_image_url(html: str) -> Optional[str]:
    """Extract the og:image meta content value from page HTML, or None if absent."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'lxml')
    tag = soup.find('meta', attrs={'property': 'og:image'})
    if tag is None:
        return None
    return (tag.get('content') or '').strip() or None


def prepare_image(image_url: str) -> ImageResult:
    """Download image_url, resize to ~180px on the longer edge, re-encode as JPEG.

    Never raises — returns Download Failed on any error.
    Reuses the shared fetch_url rate-limiter for the download.
    """
    try:
        from io import BytesIO
        from PIL import Image as PILImage

        img_data, _err = fetch_url(image_url, binary=True)
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

    def get_image_url(self, html: str, page_url: str) -> Optional[str]:
        """Return the best candidate image URL from already-fetched page HTML, or None.

        Default: og:image meta tag, normalised to an absolute URL.
        Override in adapters where og:image is absent or malformed.
        """
        raw = get_og_image_url(html)
        if not raw:
            return None
        return normalise_image_url(raw, page_url)


class StubAdapter(BaseAdapter):
    """Placeholder for brands whose adapter has not been implemented yet."""

    def fetch_dimensions(self, product_url: str) -> DimensionResult:
        return DimensionResult(
            confidence='Not Found',
            source_url=product_url,
            reason='Adapter not yet implemented',
        )
