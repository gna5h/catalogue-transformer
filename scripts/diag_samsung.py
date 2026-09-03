"""Diagnostic: run SamsungAdapter against a known-good consumer URL with full traceback visibility."""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from adapters.base import fetch_url
from adapters.samsung import SamsungAdapter, _from_spec_table, _apply_plausibility
from bs4 import BeautifulSoup

URL = 'https://www.samsung.com/nz/business/dishwashers/freestanding/60cm-freestanding-dishwasher-14-place-settings-black-dw60m6055fg-sa/'

print(f'=== Diagnostic: SamsungAdapter ===')
print(f'URL: {URL}\n')

# ── Step 0: Raw fetch diagnostics ───────────────────────────────────────────
# Bypass fetch_url() to capture redirect chains and effective URLs.
# fetch_url() discards resp.url and resp.history on success — this is the
# only way to see whether /business/ URLs are being silently redirected.

import time as _time
try:
    from curl_cffi import requests as _r
    _IMPERSONATE = 'chrome124'
except ImportError:
    import requests as _r          # type: ignore[no-redef]
    _IMPERSONATE = None

_RAW_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-NZ,en;q=0.9',
}

# Pull real URLs from the failing batch (business) and any passing batch (control).
# Slug-only forms are used here; Samsung typically 301s the short form to the full
# slug URL on the same host, so redirect visibility still works.
EXTRA_BUSINESS_URLS = [
    ('fridge RS64T5F01B4SA',
     'https://www.samsung.com/nz/business/refrigerators/french-door/rs64t5f01b4-sa/'),
    ('washer WA14A8377GW',
     'https://www.samsung.com/nz/business/washing-machines/top-load/wa14a8377gw-sa/'),
]
# Consumer (non-/business/) equivalent of the primary test URL — derived via the
# same replace('/business/', '/', 1) logic used by get_image_url().
CONTROL_URL = (
    'dishwasher DW60M6055FG consumer',
    'https://www.samsung.com/nz/dishwashers/freestanding/'
    '60cm-freestanding-dishwasher-14-place-settings-black-dw60m6055fg-sa/',
)


def _raw_diag(label: str, url: str) -> None:
    print(f'\n  [{label}]')
    print(f'  Requested:    {url}')
    kwargs: dict = dict(headers=_RAW_HEADERS, timeout=10, allow_redirects=True)
    if _IMPERSONATE:
        kwargs['impersonate'] = _IMPERSONATE
    try:
        resp = _r.get(url, **kwargs)
    except Exception as exc:
        print(f'  EXCEPTION: {exc}')
        return
    print(f'  Status:       {resp.status_code}')
    print(f'  Effective URL:{resp.url}')
    redirected = getattr(resp, 'history', [])
    if redirected:
        print(f'  Redirect chain:')
        for hop in redirected:
            print(f'    {hop.status_code} → {hop.url}')
    else:
        print(f'  Redirects:    none')
    body = resp.text if not isinstance(resp.content, bytes) or resp.text else ''
    print(f'  Body length:  {len(body)} chars')
    print(f'  "Net Width":  {"Net Width" in body}')


print('\n--- Step 0: Raw fetch diagnostics (redirect visibility) ---')
_raw_diag('business — dishwasher DW60M6055FG (primary test)', URL)
_time.sleep(1.5)
for _label, _url in EXTRA_BUSINESS_URLS:
    _raw_diag(f'business — {_label}', _url)
    _time.sleep(1.5)
_raw_diag(f'non-business control — {CONTROL_URL[0]}', CONTROL_URL[1])

# Step 1: Fetch HTML
print('--- Step 1: fetch_url ---')
html, err = fetch_url(URL)
print(f'html fetched: {html is not None}')
print(f'error:        {err!r}')
if html is None:
    print('Cannot proceed — fetch failed.')
    sys.exit(1)

print(f'HTML length:  {len(html)} chars')
dt_count = html.count('<dt')
dd_count = html.count('<dd')
print(f'<dt> tags in raw HTML: {dt_count}')
print(f'<dd> tags in raw HTML: {dd_count}')

# Step 2: Dimension parsing (bypass outer try/except)
print('\n--- Step 2: dimension parsing ---')
try:
    soup = BeautifulSoup(html, 'lxml')
    result = _from_spec_table(soup, URL)
    print(f'_from_spec_table: {result}')
    if result:
        result = _apply_plausibility(result)
        print(f'after plausibility: confidence={result.confidence} H={result.height} W={result.width} D={result.depth}')
except Exception:
    print('EXCEPTION in dimension parsing:')
    traceback.print_exc()

# Step 2b: get_image_url in isolation (bypass outer try/except)
print('\n--- Step 2b: get_image_url (isolated) ---')
adapter = SamsungAdapter()
try:
    img_url = adapter.get_image_url(html, URL)
    print(f'result: {img_url!r}')
except Exception:
    print('EXCEPTION:')
    traceback.print_exc()

# Step 3: get_image_url (bypass outer try/except)
print('\n--- Step 3: get_image_url ---')
try:
    img_url = adapter.get_image_url(html, URL)
    print(f'get_image_url result: {img_url!r}')
except Exception:
    print('EXCEPTION in get_image_url:')
    traceback.print_exc()

# Step 4: Full fetch_dimensions (with its own try/except — inspect reason on failure)
print('\n--- Step 4: full fetch_dimensions ---')
full_result = adapter.fetch_dimensions(URL)
print(f'confidence:   {full_result.confidence}')
print(f'height:       {full_result.height}')
print(f'width:        {full_result.width}')
print(f'depth:        {full_result.depth}')
print(f'image_status: {full_result.image_status}')
print(f'reason:       {full_result.reason!r}')
print(f'raw_text:     {full_result.raw_text!r}')
