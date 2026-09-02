"""Tests for WestinghouseAdapter — covers all confirmed product category formats."""

from __future__ import annotations

from unittest.mock import patch

from adapters.westinghouse import WestinghouseAdapter
from adapters.base import DimensionResult

URL = 'https://www.westinghouse.co.nz/test/wsu9999xyz/'

adapter = WestinghouseAdapter()


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _html_spec(*pairs: tuple[str, str]) -> str:
    """Build a minimal page with the Westinghouse specification-category list structure."""
    items = ''.join(
        f'<li class="specification-item js-content-by-country">'
        f'<span class="units">{label}</span>'
        f'<span class="dimension">{value}</span>'
        f'</li>'
        for label, value in pairs
    )
    return (
        '<html><body>'
        f'<ul class="specification-category">{items}</ul>'
        '</body></html>'
    )


def _fetch(html: str) -> tuple[str, str]:
    return (html, '')


def _fetch_none(reason: str) -> tuple[None, str]:
    return (None, reason)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestWestinghouseAdapterParsing:

    def test_dishwasher_height_adjustment_applied(self):
        """Total height 815mm + Height adjustment 50mm → height=865; raw_text documents basis."""
        html = _html_spec(
            ('Total height (mm)', '815 mm'),
            ('Total width (mm)',  '598 mm'),
            ('Total depth (mm)',  '570 mm'),
            ('Height adjustment (mm)', '50'),
        )
        with patch('adapters.westinghouse.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '865'
        assert result.width  == '598'
        assert result.depth  == '570'
        assert '815' in result.raw_text
        assert '50'  in result.raw_text
        assert '865' in result.raw_text

    def test_oven_no_adjustment_resolved(self):
        """Oven: Total height/width/depth, no Height adjustment → height = stated value."""
        html = _html_spec(
            ('Total height (mm)', '596 mm'),
            ('Total width (mm)',  '895 mm'),
            ('Total depth (mm)',  '573 mm'),
        )
        with patch('adapters.westinghouse.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '596'
        assert result.width  == '895'
        assert result.depth  == '573'

    def test_fridge_cabinet_variants_excluded(self):
        """Cabinet height/width/depth rows excluded; Total * rows used."""
        html = _html_spec(
            ('Total height (mm)',   '1795 mm'),
            ('Cabinet height (mm)', '1775'),
            ('Total width (mm)',    '896 mm'),
            ('Cabinet width (mm)',  '890'),
            ('Total depth (mm)',    '726 mm'),
            ('Cabinet depth (mm)',  '640'),
            ('Depth door open 90degree (mm)', '1104'),
        )
        with patch('adapters.westinghouse.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '1795'
        assert result.width  == '896'
        assert result.depth  == '726'

    def test_washer_lid_and_hose_dims_excluded(self):
        """Height with lid open and Depth with hoses are excluded; Total * dims used."""
        html = _html_spec(
            ('Total height (mm)',       '1095 mm'),
            ('Total width (mm)',        '670 mm'),
            ('Total depth (mm)',        '720 mm'),
            ('Height with lid open (mm)', '1460'),
            ('Depth with hoses (mm)',   '745'),
        )
        with patch('adapters.westinghouse.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '1095'
        assert result.width  == '670'
        assert result.depth  == '720'

    def test_cooktop_total_product_labels_resolved_low_height(self):
        """Cooktop uses 'Total product height/width/depth' labels; height 51mm → Needs Review."""
        html = _html_spec(
            ('Total product height (mm)', '51 mm'),
            ('Total product width (mm)',  '590 mm'),
            ('Total product depth (mm)',  '520 mm'),
            ('Cut out width (mm)',  '560'),
            ('Cut out depth (mm)',  '490'),
        )
        with patch('adapters.westinghouse.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Needs Review'
        assert result.height == '51'
        assert result.width  == '590'
        assert result.depth  == '520'
        assert '51' in result.reason

    def test_dryer_total_labels_resolved(self):
        """Dryer: Total height/width/depth, no adjustment → Resolved."""
        html = _html_spec(
            ('Total height (mm)', '850 mm'),
            ('Total width (mm)',  '600 mm'),
            ('Total depth (mm)',  '662 mm'),
        )
        with patch('adapters.westinghouse.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '850'
        assert result.width  == '600'
        assert result.depth  == '662'

    def test_zero_height_adjustment_not_applied(self):
        """Height adjustment of 0 is ignored; Total height used as-is."""
        html = _html_spec(
            ('Total height (mm)', '850 mm'),
            ('Total width (mm)',  '600 mm'),
            ('Total depth (mm)',  '580 mm'),
            ('Height adjustment (mm)', '0'),
        )
        with patch('adapters.westinghouse.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '850'

    def test_no_dimension_data_not_found(self):
        """Page with no specification-category section → Not Found."""
        html = '<html><body><p>No specs here.</p></body></html>'
        with patch('adapters.westinghouse.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_partial_dims_needs_review(self):
        """Only Total height and Total width present (no depth) → Needs Review."""
        html = _html_spec(
            ('Total height (mm)', '815 mm'),
            ('Total width (mm)',  '598 mm'),
        )
        with patch('adapters.westinghouse.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Needs Review'
        assert result.height == '815'
        assert result.width  == '598'
        assert result.depth  is None

    def test_fetch_timeout_returns_not_found(self):
        """fetch_url timeout → Not Found with reason preserved."""
        with patch('adapters.westinghouse.fetch_url', return_value=_fetch_none('timeout')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert result.reason == 'timeout'

    def test_fetch_403_returns_not_found(self):
        """fetch_url HTTP 403 → Not Found with 'access refused' in reason."""
        with patch('adapters.westinghouse.fetch_url',
                   return_value=_fetch_none('access refused (HTTP 403)')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'access refused' in result.reason

    def test_unexpected_exception_caught(self):
        """BeautifulSoup raises RuntimeError → Not Found with 'RuntimeError' in reason."""
        with patch('adapters.westinghouse.fetch_url', return_value=_fetch('<html></html>')):
            with patch('adapters.westinghouse.BeautifulSoup',
                       side_effect=RuntimeError('parser error')):
                result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'RuntimeError' in result.reason
