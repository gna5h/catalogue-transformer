"""Unit tests for the LG adapter (adapters/lg.py).

All HTTP calls are mocked via unittest.mock.patch on adapters.lg.fetch_url.
No network access is required.
"""

from unittest.mock import patch

import pytest

from adapters.lg import LGAdapter
from adapters.base import DimensionResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_URL = 'http://lg.com/nz/example-product'


def _html_with_dl(*pairs: tuple[str, str]) -> str:
    """Return minimal HTML containing a <dl> with the given (dt, dd) pairs."""
    items = ''.join(
        f'<dt>{label}</dt><dd>{value}</dd>'
        for label, value in pairs
    )
    return f'<html><body><dl>{items}</dl></body></html>'


def _fetch(html: str):
    """Return a mock return value for fetch_url that yields the given HTML."""
    return (html, '')


def _fetch_none(reason: str):
    """Return a mock return value for fetch_url that signals a failed fetch."""
    return (None, reason)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLGAdapterParsing:

    def setup_method(self):
        self.adapter = LGAdapter()

    # 1. Combined W×H×D label — axes mapped in label order
    def test_combined_wxhxd_label(self):
        html = _html_with_dl(('Net Dimensions (W x H x D)', '595 x 820 x 540'))
        with patch('adapters.lg.fetch_url', return_value=_fetch(html)):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Resolved'
        assert result.width  == '595'
        assert result.height == '820'
        assert result.depth  == '540'

    # 2. Different axis order — H and W are NOT swapped relative to position
    def test_different_axis_order_hxwxd(self):
        html = _html_with_dl(('Net Dimensions (H x W x D)', '820 x 595 x 540'))
        with patch('adapters.lg.fetch_url', return_value=_fetch(html)):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Resolved'
        assert result.height == '820'
        assert result.width  == '595'
        assert result.depth  == '540'

    # 3. Separate single-axis labels → Resolved
    def test_separate_single_axis_labels(self):
        html = _html_with_dl(
            ('Width (mm)', '595'),
            ('Height (mm)', '820'),
            ('Depth (mm)', '540'),
        )
        with patch('adapters.lg.fetch_url', return_value=_fetch(html)):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Resolved'
        assert result.width  == '595'
        assert result.height == '820'
        assert result.depth  == '540'

    # 4. cm unit conversion → values multiplied by 10 to produce mm
    def test_cm_unit_conversion(self):
        html = _html_with_dl(('Net Dimensions (W x H x D) (cm)', '60 x 82 x 54'))
        with patch('adapters.lg.fetch_url', return_value=_fetch(html)):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Resolved'
        assert result.width  == '600'
        assert result.height == '820'
        assert result.depth  == '540'

    # 5. Cavity label with overall convention → skipped → Not Found
    def test_cavity_label_skipped_in_overall_convention(self):
        html = _html_with_dl(('Cutout Dimensions (W x H x D)', '595 x 820 x 540'))
        with patch('adapters.lg.fetch_url', return_value=_fetch(html)):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Not Found'

    # 6. Two conflicting overall dimension rows → Needs Review
    def test_conflict_detection(self):
        html = _html_with_dl(
            ('Net Dimensions (W x H x D)', '595 x 820 x 540'),
            ('Net Dimensions (W x H x D)', '600 x 850 x 560'),
        )
        with patch('adapters.lg.fetch_url', return_value=_fetch(html)):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Needs Review'

    # 7. Only W and D found, H missing → Needs Review
    def test_partial_dims_needs_review(self):
        html = _html_with_dl(
            ('Width (mm)', '595'),
            ('Depth (mm)', '540'),
        )
        with patch('adapters.lg.fetch_url', return_value=_fetch(html)):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Needs Review'
        assert result.width == '595'
        assert result.depth == '540'
        assert result.height is None

    # 8. fetch_url returns None (timeout) → Not Found, reason='timeout'
    def test_fetch_timeout_returns_not_found(self):
        with patch('adapters.lg.fetch_url', return_value=_fetch_none('timeout')):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Not Found'
        assert result.reason == 'timeout'

    # 9. fetch_url returns 403 → Not Found, reason contains 'access refused'
    def test_fetch_403_returns_not_found(self):
        with patch('adapters.lg.fetch_url', return_value=_fetch_none('access refused (HTTP 403)')):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Not Found'
        assert 'access refused' in result.reason

    # 10. Page with no dimension data → Not Found
    def test_no_dimension_data_on_page(self):
        html = '<html><body><p>No specs here.</p></body></html>'
        with patch('adapters.lg.fetch_url', return_value=_fetch(html)):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Not Found'

    # 11. Unhandled exception inside fetch_dimensions → caught, returns Not Found
    def test_unhandled_exception_is_caught(self):
        # BeautifulSoup is called right after fetch_url returns content;
        # raising here proves the belt-and-suspenders try/except works.
        with patch('adapters.lg.fetch_url', return_value=_fetch('<html></html>')):
            with patch('adapters.lg.BeautifulSoup', side_effect=RuntimeError('parser exploded')):
                result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Not Found'
        assert 'RuntimeError' in result.reason

    # 12. Packing dimensions excluded, product dimensions resolved
    def test_packing_dims_excluded(self):
        html = _html_with_dl(
            ('Packing Dimensions (W x H x D) (mm)', '540 x 292 x 386'),
            ('Product Dimensions (W x H x D) (mm)', '454 x 261 x 328'),
            ('Cavity Dimension (W x H x D) (mm)',   '317 x 204 x 294'),
        )
        with patch('adapters.lg.fetch_url', return_value=_fetch(html)):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Resolved'
        assert result.width  == '454'
        assert result.height == '261'
        assert result.depth  == '328'

    # 13. Duplicate identical product dimensions not treated as conflict
    def test_duplicate_identical_not_conflict(self):
        html = _html_with_dl(
            ('Product Dimensions (W x H x D) (mm)', '454 x 261 x 328'),
            ('Product Dimensions (W x H x D) (mm)', '454 x 261 x 328'),
        )
        with patch('adapters.lg.fetch_url', return_value=_fetch(html)):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Resolved'

    # 14. "Gross Dimensions" excluded (same as packing)
    def test_gross_dims_excluded(self):
        html = _html_with_dl(
            ('Gross Dimensions (W x H x D) (mm)', '550 x 310 x 400'),
            ('Net Dimensions (W x H x D) (mm)',   '454 x 261 x 328'),
        )
        with patch('adapters.lg.fetch_url', return_value=_fetch(html)):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Resolved'
        assert result.width  == '454'
        assert result.height == '261'
        assert result.depth  == '328'

    # 15. LG AEM CMS structure (c-text-contents / cmp-title__text / cmp-text) → Resolved
    def test_aem_cms_structure_resolved(self):
        def _aem_item(label: str, value: str) -> str:
            return (
                f'<div class="c-text-contents item">'
                f'<div class="title c-text-contents__headline">'
                f'<div class="cmp-title"><strong class="cmp-title__text">{label}</strong></div>'
                f'</div>'
                f'<div class="text c-text-contents__bodycopy">'
                f'<div class="cmp-text">{value}</div>'
                f'</div>'
                f'</div>'
            )
        html = '<html><body>' + _aem_item('Product Dimensions (W x H x D) (mm)', '454 x 261 x 328') + '</body></html>'
        with patch('adapters.lg.fetch_url', return_value=_fetch(html)):
            result = self.adapter.fetch_dimensions(_URL)
        assert result.confidence == 'Resolved'
        assert result.width  == '454'
        assert result.height == '261'
        assert result.depth  == '328'
