"""Diagnostic: run SamsungAdapter against a known-good consumer URL with full traceback visibility."""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from adapters.base import fetch_url
from adapters.samsung import SamsungAdapter, _from_spec_table, _apply_plausibility
from bs4 import BeautifulSoup

URL = 'https://www.samsung.com/nz/dishwashers/freestanding/13-place-settings-white-dw60m6045fw-sa/'

print(f'=== Diagnostic: SamsungAdapter ===')
print(f'URL: {URL}\n')

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

# Step 3: get_image_url (bypass outer try/except)
print('\n--- Step 3: get_image_url ---')
adapter = SamsungAdapter()
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
