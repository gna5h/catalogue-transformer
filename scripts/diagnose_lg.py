"""Diagnostic: confirm whether LG spec data is in raw HTML or requires JS rendering."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from adapters.base import fetch_url

URL = 'https://www.lg.com/nz/microwave-ovens/ms2036ndb/'
html, err = fetch_url(URL)

if html is None:
    print(f'FETCH FAILED: {err}')
    sys.exit(1)

if 'Product Dimensions' in html:
    print('✓ "Product Dimensions" IS in raw HTML → Step 2B (parsing fix only)')
    idx = html.index('Product Dimensions')
    print(html[max(0, idx - 200):idx + 300])
else:
    print('✗ "Product Dimensions" NOT in raw HTML → Step 2A (JS rendering needed)')
    for marker in ('graphqlEndpoint', 'meta-store-config', '__NEXT_DATA__', 'jcr:content'):
        if marker in html:
            print(f'  Found page marker: {marker}')
