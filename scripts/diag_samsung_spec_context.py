"""Find where 'Net Width', 'Net Height', 'Net Depth' appear in the Samsung page HTML."""
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
from adapters.base import fetch_url

URL = 'https://www.samsung.com/nz/business/dishwashers/freestanding/60cm-freestanding-dishwasher-14-place-settings-black-dw60m6055fg-sa/'
print(f'Fetching {URL}')
html, err = fetch_url(URL)
if html is None:
    print(f'Fetch failed: {err}')
    sys.exit(1)

for kw in ['Net Width', 'Net Height', 'Net Depth']:
    idx = html.find(kw)
    if idx == -1:
        print(f'{kw!r}: NOT FOUND')
    else:
        # Show 300 chars before and after
        start = max(0, idx - 300)
        end   = min(len(html), idx + len(kw) + 300)
        snippet = html[start:end]
        print(f'\n=== {kw!r} at position {idx} ===')
        print(repr(snippet))

# Also look at the single <dl> tag
dl_idx = html.find('<dl')
if dl_idx != -1:
    print(f'\n=== <dl> at position {dl_idx} ===')
    print(repr(html[dl_idx:dl_idx+500]))
