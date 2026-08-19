"""Full-batch F&P cache-bust and fresh-fetch verification.

Clears all Fisher & Paykel entries from the SQLite cache, re-fetches every
URL via FPAdapter, stores the new results back into the cache, and reports:
  - Fresh fetch count vs. expected
  - Confidence breakdown by sheet
  - Any row where H/W/D is still non-numeric after the fresh fetch

Usage:
    python scripts/verify_fp_full_batch.py
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import cache as dim_cache
from adapters.fp import FPAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NUMERIC_RE = re.compile(r'^\d+$')


def is_numeric(val: str | None) -> bool:
    return val is None or bool(_NUMERIC_RE.match(val))


def infer_sheet(url: str) -> str:
    if '/dishwashing/' in url:
        return 'DishWashers'
    if '/cooling/' in url:
        return 'Fridges & Freezers'
    if '/cooktops/' in url:
        return 'Cooktops'
    if '/ovens/' in url or '/freestanding-cookers/' in url:
        return 'Ovens'
    if '/laundry/' in url or '/outlet/' in url:
        return 'Laundry'
    return 'Other'


def extract_sku(url: str) -> str:
    """Best-effort SKU from URL slug (second-to-last hyphen-delimited token)."""
    slug = url.rstrip('/').split('/')[-1].replace('.html', '')
    parts = slug.rsplit('-', 1)[0].rsplit('-', 1)
    return parts[-1].upper() if parts else slug.upper()


# ---------------------------------------------------------------------------
# Step 1 — collect all F&P URLs from cache
# ---------------------------------------------------------------------------

db_path = Path(__file__).parent.parent / 'cache' / 'dimension_cache.db'
conn = sqlite3.connect(str(db_path))
all_fp = conn.execute(
    "SELECT url, confidence, height FROM dim_cache WHERE url LIKE '%fisherpaykel%'"
).fetchall()
conn.close()

urls = [row[0] for row in all_fp]
print(f'F&P entries found in cache : {len(urls)}')

if len(urls) != 86:
    print(f'WARNING: expected 86, got {len(urls)} — proceeding anyway')

# ---------------------------------------------------------------------------
# Step 2 — delete all F&P entries from cache
# ---------------------------------------------------------------------------

conn = sqlite3.connect(str(db_path))
conn.execute("DELETE FROM dim_cache WHERE url LIKE '%fisherpaykel%'")
conn.commit()
conn.close()
print(f'Deleted {len(urls)} entries. Beginning fresh fetches...\n')

# ---------------------------------------------------------------------------
# Step 3 — fresh fetch each URL
# ---------------------------------------------------------------------------

adapter = FPAdapter()

results: list[dict] = []
for i, url in enumerate(urls, 1):
    result = adapter.fetch_dimensions(url)
    dim_cache.store_result(url, result)
    sheet = infer_sheet(url)
    sku   = extract_sku(url)
    results.append({
        'url':        url,
        'sheet':      sheet,
        'sku':        sku,
        'confidence': result.confidence,
        'height':     result.height,
        'width':      result.width,
        'depth':      result.depth,
        'raw_text':   result.raw_text,
        'reason':     result.reason,
    })
    flag = ''
    for axis, val in [('H', result.height), ('W', result.width), ('D', result.depth)]:
        if not is_numeric(val):
            flag += f'  *** NON-NUMERIC {axis}={val!r}'
    print(f'[{i:02d}/{len(urls)}] {sheet:<22} {sku:<14} {result.confidence:<14}'
          f'H={result.height or "—":>6} W={result.width or "—":>6} D={result.depth or "—":>6}'
          f'{flag}')

# ---------------------------------------------------------------------------
# Step 4 — summary
# ---------------------------------------------------------------------------

print('\n' + '=' * 70)
print('FRESH FETCH COUNT')
print('=' * 70)
print(f'  Expected : 86')
print(f'  Actual   : {len(results)}  (cache had {len(urls)} entries before purge)')

print('\n' + '=' * 70)
print('CONFIDENCE BREAKDOWN BY SHEET')
print('=' * 70)
from collections import defaultdict
sheet_conf: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
for r in results:
    sheet_conf[r['sheet']][r['confidence']] += 1

for sheet in sorted(sheet_conf):
    counts = sheet_conf[sheet]
    total  = sum(counts.values())
    print(f'  {sheet:<25} total={total:2d}'
          f'  Resolved={counts.get("Resolved", 0):2d}'
          f'  NeedsReview={counts.get("Needs Review", 0):2d}'
          f'  NotFound={counts.get("Not Found", 0):2d}')

print('\n' + '=' * 70)
print('NON-NUMERIC DIMENSION VALUES (bugs to investigate)')
print('=' * 70)
non_numeric = []
for r in results:
    flags = []
    for axis, val in [('Height', r['height']), ('Width', r['width']), ('Depth', r['depth'])]:
        if not is_numeric(val):
            flags.append(f'{axis}={val!r}')
    if flags:
        non_numeric.append((r['sheet'], r['sku'], r['url'], ', '.join(flags)))

if non_numeric:
    for sheet, sku, url, issues in non_numeric:
        print(f'  [{sheet}] {sku}')
        print(f'    {issues}')
        print(f'    {url}')
else:
    print('  None — all dimension values are plain numbers or None.')

print('\n' + '=' * 70)
print('DISHWASHER SPOT-CHECK (7 previously verified rows)')
print('=' * 70)
dw_skus = {'DW60UZT4B2', 'DD60DTX6I1', 'DD60D4ZB9', 'DD60D4NX9', 'DD60D4NB9', 'DD60DI9', 'DW60U4EI3'}
dw_expected = {
    'DW60UZT4B2': '917', 'DD60DTX6I1': '925',
    'DD60D4ZB9': '880', 'DD60D4NX9': '880', 'DD60D4NB9': '880',
    'DD60DI9': '880', 'DW60U4EI3': '880',
}
for r in results:
    if r['sheet'] == 'DishWashers':
        sku = r['sku']
        exp = dw_expected.get(sku)
        if exp is not None:
            status = 'OK' if r['height'] == exp else f'MISMATCH (expected {exp})'
            print(f'  {sku:<14} height={r["height"]:>6}  {status}')
