"""Base adapter interface, shared DimensionResult type, and HTTP helper."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

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
    height:     Optional[str] = None
    width:      Optional[str] = None
    depth:      Optional[str] = None
    confidence: str = 'Not Found'
    source_url: str = ''
    raw_text:   str = ''   # raw spec snippet for audit / debugging
    reason:     str = ''   # human-readable explanation for non-Resolved outcomes


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
