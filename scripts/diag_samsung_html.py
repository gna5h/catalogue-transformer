"""Investigate Samsung page structure: what does the raw HTML actually contain for specs?"""
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from adapters.base import fetch_url
from bs4 import BeautifulSoup

URL = 'https://www.samsung.com/nz/dishwashers/freestanding/13-place-settings-white-dw60m6045fw-sa/'

print(f'Fetching {URL}')
html, err = fetch_url(URL)
if html is None:
    print(f'Fetch failed: {err}')
    sys.exit(1)

print(f'HTML: {len(html)} chars\n')

soup = BeautifulSoup(html, 'lxml')

# --- Search for spec/dimension keywords ---
print('=== Keyword scan in raw HTML ===')
for kw in ['Net Width', 'Net Height', 'Net Depth', 'WxHxD', 'wxhxd',
           'dimension', 'Dimension', 'specification', 'Specification',
           'spec', 'mm', '<dt', '<dl']:
    count = html.count(kw)
    if count:
        print(f'  {kw!r}: {count} occurrences')

# --- JSON-LD blocks ---
print('\n=== JSON-LD script blocks ===')
for i, script in enumerate(soup.find_all('script', type='application/ld+json')):
    text = (script.string or '').strip()
    print(f'  Block {i}: {len(text)} chars — first 200: {text[:200]!r}')

# --- Scripts containing "dimension" or "width" or "spec" ---
print('\n=== Inline scripts mentioning dimensions ===')
for script in soup.find_all('script'):
    text = (script.string or '')
    if any(kw in text for kw in ['NetWidth', 'NetHeight', 'NetDepth', 'wxhxd', 'WxHxD',
                                   'net_width', 'net_height', '"width"', '"height"', '"depth"',
                                   'specification', 'productSpecification']):
        src = script.get('src', '')
        if not src:  # inline only
            snippet = text.strip()[:400]
            print(f'\n  [inline, {len(text)} chars]: {snippet!r}')

# --- Look for any element containing "mm" values near "Net" ---
print('\n=== Elements with "Net" near "mm" ===')
for tag in soup.find_all(string=re.compile(r'\bNet\b.*\bmm\b', re.IGNORECASE)):
    parent = tag.parent
    print(f'  <{parent.name}> text: {str(tag).strip()[:120]!r}')

# --- Data attributes that might hold spec data ---
print('\n=== data- attributes on spec-looking elements ===')
for tag in soup.find_all(attrs={'data-spec': True}):
    print(f'  {tag.name} data-spec={tag["data-spec"]!r}: {tag.get_text(strip=True)[:80]}')

for tag in soup.find_all(attrs={'data-testid': re.compile(r'spec|dimension|feature', re.I)}):
    print(f'  data-testid={tag["data-testid"]!r}: {tag.get_text(strip=True)[:80]}')

# --- __NEXT_DATA__ or __INITIAL_STATE__ (Next.js / React) ---
print('\n=== __NEXT_DATA__ / __INITIAL_STATE__ ===')
for pattern in [r'__NEXT_DATA__\s*=\s*(\{.{0,200})', r'__INITIAL_STATE__\s*=\s*(\{.{0,200})']:
    m = re.search(pattern, html)
    if m:
        print(f'  Found: {m.group(0)[:200]!r}')
    else:
        print(f'  Not found: {pattern!r}')

# --- Any table-like structure ---
print('\n=== Tables, lists, or structured spec containers ===')
for tag_name in ['table', 'ul', 'ol']:
    tags = soup.find_all(tag_name)
    print(f'  <{tag_name}>: {len(tags)} found')

# Look for class names that suggest a spec panel
for tag in soup.find_all(class_=re.compile(r'spec|dimension|feature|detail', re.I)):
    text = tag.get_text(strip=True)[:100]
    if text:
        print(f'  class={tag.get("class")}: {text!r}')
    if len(list(tag.find_all(class_=re.compile(r'spec|dim', re.I)))) > 0:
        break  # avoid flooding
