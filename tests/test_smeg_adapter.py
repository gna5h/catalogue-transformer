"""Tests for SmegAdapter — combined label, individual labels, exclusions."""

from __future__ import annotations

from unittest.mock import patch

from adapters.smeg import SmegAdapter
from adapters.base import DimensionResult

URL = 'https://www.smeg.com/nz/products/TEST9999'

adapter = SmegAdapter()


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _html(*rows: tuple[str, str]) -> str:
    """Build a Smeg spec page with div.detail + span.detail__label/txt rows."""
    items = ''.join(
        f'<div class="detail">'
        f'<span class="detail__label">{label}</span>'
        f'<span class="detail__txt">{value}</span>'
        f'</div>'
        for label, value in rows
    )
    return f'<html><body>{items}</body></html>'


def _fetch(html: str) -> tuple[str, str]:
    return (html, '')


def _fetch_none(reason: str) -> tuple[None, str]:
    return (None, reason)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestSmegAdapterParsing:

    def test_combined_h_w_d_resolved(self):
        """Combined 'HxWxD' label with '848x598x600 mm' → Resolved, axes correctly assigned."""
        html = _html(
            ('Dimensions of the product HxWxD (mm):', '848x598x600 mm'),
        )
        with patch('adapters.smeg.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '848'
        assert result.width  == '598'
        assert result.depth  == '600'
        assert '848x598x600 mm' in result.raw_text

    def test_combined_axis_order_respected(self):
        """'WxHxD' order in label → width first, then height, then depth."""
        html = _html(
            ('Dimensions WxHxD (mm):', '598x848x600 mm'),
        )
        with patch('adapters.smeg.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '848'
        assert result.width  == '598'
        assert result.depth  == '600'

    def test_commercial_labels_excluded(self):
        """Commercial height/width (rounded nominal sizes) are excluded; real dims used."""
        html = _html(
            ('Commercial height:', '85 cm'),
            ('Commercial width:',  '60 cm'),
            ('Dimensions of the product HxWxD (mm):', '848x598x600 mm'),
        )
        with patch('adapters.smeg.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '848'
        assert result.width  == '598'
        assert result.depth  == '600'

    def test_packed_and_packaged_labels_excluded(self):
        """Packed width/depth and packaged labels excluded; real dims used → Resolved."""
        html = _html(
            ('Dimensions of the product HxWxD (mm):', '848x598x600 mm'),
            ('Dimensions of the packed product (mm):', '920x650x680 mm'),
            ('Packed width:', '650 mm'),
            ('Packaged depth:', '680 mm'),
            ('Height (mm) packed:', '920 mm'),
        )
        with patch('adapters.smeg.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '848'
        assert result.width  == '598'
        assert result.depth  == '600'

    def test_individual_labels_resolved(self):
        """'Product Height', 'Width', 'Depth' individual labels → Resolved."""
        html = _html(
            ('Width (mm):', '598 mm'),
            ('Depth (mm):', '600 mm'),
            ('Product Height (mm):', '848 mm'),
        )
        with patch('adapters.smeg.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '848'
        assert result.width  == '598'
        assert result.depth  == '600'

    def test_combined_takes_priority_over_individual(self):
        """Combined label found first; individual axis values not used."""
        html = _html(
            ('Dimensions of the product HxWxD (mm):', '848x598x600 mm'),
            ('Product Height (mm):', '999 mm'),
            ('Width (mm):', '999 mm'),
            ('Depth (mm):', '999 mm'),
        )
        with patch('adapters.smeg.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.height == '848'
        assert result.width  == '598'
        assert result.depth  == '600'

    def test_only_packed_dims_not_found(self):
        """Only packed/commercial rows present → Not Found."""
        html = _html(
            ('Commercial height:', '85 cm'),
            ('Commercial width:',  '60 cm'),
            ('Dimensions of the packed product (mm):', '920x650x680 mm'),
        )
        with patch('adapters.smeg.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_no_dimension_data_not_found(self):
        """Page with no spec rows → Not Found."""
        html = '<html><body><p>No specs here.</p></body></html>'
        with patch('adapters.smeg.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_fetch_timeout_returns_not_found(self):
        """fetch_url timeout → Not Found with reason preserved."""
        with patch('adapters.smeg.fetch_url', return_value=_fetch_none('timeout')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert result.reason == 'timeout'

    def test_fetch_403_returns_not_found(self):
        """fetch_url HTTP 403 → Not Found with 'access refused' in reason."""
        with patch('adapters.smeg.fetch_url',
                   return_value=_fetch_none('access refused (HTTP 403)')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'access refused' in result.reason

    def test_unexpected_exception_caught(self):
        """BeautifulSoup raises RuntimeError → Not Found with 'RuntimeError' in reason."""
        with patch('adapters.smeg.fetch_url', return_value=_fetch('<html></html>')):
            with patch('adapters.smeg.BeautifulSoup',
                       side_effect=RuntimeError('parser error')):
                result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'RuntimeError' in result.reason
