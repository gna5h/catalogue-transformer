"""Tests for ElectroluxAdapter — covers both strategies and all edge cases."""

from __future__ import annotations

from unittest.mock import patch

from adapters.electrolux import ElectroluxAdapter
from adapters.base import DimensionResult

URL = 'https://www.electrolux.co.nz/test/evem9999xyz/'

adapter = ElectroluxAdapter()


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _html_dim_card(*items: str) -> str:
    """Build a page with the Electrolux Dimension card mini-section.

    Each item is a string like '595 mm (W)'.
    """
    lis = ''.join(
        f'<li class="content-item js-content-by-country" data-countries="">{item}</li>'
        for item in items
    )
    return (
        '<html><body>'
        '<div class="content">'
        '<h3 class="title">Dimension</h3>'
        f'<ul class="content-category">{lis}</ul>'
        '</div>'
        '</body></html>'
    )


def _html_spec(*pairs: tuple[str, str]) -> str:
    """Build a page with the Electrolux specification-category list structure."""
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


def _html_both(card_items: list[str], spec_pairs: list[tuple[str, str]]) -> str:
    """Build a page with both the dim card and the spec table."""
    lis_card = ''.join(
        f'<li class="content-item js-content-by-country">{item}</li>'
        for item in card_items
    )
    lis_spec = ''.join(
        f'<li class="specification-item">'
        f'<span class="units">{label}</span>'
        f'<span class="dimension">{value}</span>'
        f'</li>'
        for label, value in spec_pairs
    )
    return (
        '<html><body>'
        '<div class="content">'
        '<h3 class="title">Dimension</h3>'
        f'<ul class="content-category">{lis_card}</ul>'
        '</div>'
        f'<ul class="specification-category">{lis_spec}</ul>'
        '</body></html>'
    )


def _fetch(html: str) -> tuple[str, str]:
    return (html, '')


def _fetch_none(reason: str) -> tuple[None, str]:
    return (None, reason)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestElectroluxAdapterParsing:

    def test_dim_card_all_axes_resolved(self):
        """Dimension card '595 mm (W)' / '456 mm (H)' / '571 mm (D)' → Resolved."""
        html = _html_dim_card('595 mm (W)', '456 mm (H)', '571 mm (D)')
        with patch('adapters.electrolux.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '456'
        assert result.width  == '595'
        assert result.depth  == '571'

    def test_dim_card_low_height_needs_review(self):
        """Cooktop height 51 mm is below 200 mm floor → Needs Review with reason."""
        html = _html_dim_card('590 mm (W)', '51 mm (H)', '520 mm (D)')
        with patch('adapters.electrolux.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Needs Review'
        assert result.height == '51'
        assert result.width  == '590'
        assert result.depth  == '520'
        assert '51' in result.reason

    def test_dim_card_partial_needs_review(self):
        """Dim card with only H and W (no D) → Needs Review; depth is None."""
        html = _html_dim_card('598 mm (W)', '815 mm (H)')
        with patch('adapters.electrolux.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Needs Review'
        assert result.height == '815'
        assert result.width  == '598'
        assert result.depth  is None

    def test_spec_table_total_labels_resolved(self):
        """Fallback: 'Total height (mm)' / 'Total width (mm)' / 'Total depth (mm)' → Resolved."""
        html = _html_spec(
            ('Total height (mm)', '850 mm'),
            ('Total width (mm)',  '600 mm'),
            ('Total depth (mm)',  '662 mm'),
        )
        with patch('adapters.electrolux.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '850'
        assert result.width  == '600'
        assert result.depth  == '662'

    def test_spec_table_width_without_total_resolved(self):
        """Fallback oven pattern: 'Width' (no Total) / 'Total height (mm)' / 'Total depth (mm)' → Resolved."""
        html = _html_spec(
            ('Width',             '595 mm'),
            ('Total height (mm)', '456 mm'),
            ('Total depth (mm)',  '571 mm'),
        )
        with patch('adapters.electrolux.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '456'
        assert result.width  == '595'
        assert result.depth  == '571'

    def test_spec_table_cabinet_variants_excluded(self):
        """Cabinet height/width/depth excluded; Total * rows used → Resolved."""
        html = _html_spec(
            ('Total height',      '1795 mm'),
            ('Cabinet height (mm)', '1775'),
            ('Total width',       '896 mm'),
            ('Cabinet width (mm)', '890'),
            ('Total depth',       '726 mm'),
            ('Cabinet depth (mm)', '640'),
            ('Depth door open 90degree (mm)', '1104'),
        )
        with patch('adapters.electrolux.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '1795'
        assert result.width  == '896'
        assert result.depth  == '726'

    def test_dim_card_takes_priority_over_spec_table(self):
        """When both dim card and spec table are present, card values win."""
        html = _html_both(
            card_items=['600 mm (W)', '900 mm (H)', '650 mm (D)'],
            spec_pairs=[
                ('Total height (mm)', '999 mm'),
                ('Total width (mm)',  '999 mm'),
                ('Total depth (mm)',  '999 mm'),
            ],
        )
        with patch('adapters.electrolux.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '900'
        assert result.width  == '600'
        assert result.depth  == '650'

    def test_no_dimension_data_not_found(self):
        """Page with no Dimension card and no specification-category section → Not Found."""
        html = '<html><body><p>No specs here.</p></body></html>'
        with patch('adapters.electrolux.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_fetch_timeout_returns_not_found(self):
        """fetch_url timeout → Not Found with reason preserved."""
        with patch('adapters.electrolux.fetch_url', return_value=_fetch_none('timeout')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert result.reason == 'timeout'

    def test_fetch_403_returns_not_found(self):
        """fetch_url HTTP 403 → Not Found with 'access refused' in reason."""
        with patch('adapters.electrolux.fetch_url',
                   return_value=_fetch_none('access refused (HTTP 403)')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'access refused' in result.reason

    def test_unexpected_exception_caught(self):
        """BeautifulSoup raises RuntimeError → Not Found with 'RuntimeError' in reason."""
        with patch('adapters.electrolux.fetch_url', return_value=_fetch('<html></html>')):
            with patch('adapters.electrolux.BeautifulSoup',
                       side_effect=RuntimeError('parser error')):
                result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'RuntimeError' in result.reason
