"""Tests for HaierAdapter — mirrors tests/test_fp_adapter.py pattern."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from adapters.haier import HaierAdapter
from adapters.base import DimensionResult

URL = 'https://www.haier.co.nz/dishwashing/dishwashers/test-product.html'

adapter = HaierAdapter()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _html_dim_table(*rows: tuple[str, str]) -> str:
    body = ''.join(f'<tr><td>{label}</td><td>{value}</td></tr>' for label, value in rows)
    return (
        '<html><body><h3>Product dimensions</h3>'
        '<table><thead><tr><th>Product dimensions Attributes</th><th>Value</th></tr></thead>'
        f'<tbody>{body}</tbody></table></body></html>'
    )


def _fetch(html: str) -> tuple[str, str]:
    return (html, '')


def _fetch_none(reason: str) -> tuple[None, str]:
    return (None, reason)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestHaierAdapterParsing:

    def test_table_primary_single_values(self):
        """Table is the primary source; all three axes (single mm values) → Resolved."""
        html = _html_dim_table(('Height', '820mm'), ('Width', '597mm'), ('Depth', '574mm'))
        with patch('adapters.haier.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '820'
        assert result.width == '597'
        assert result.depth == '574'

    def test_table_range_height_compact_hyphen(self):
        """Table height '820-880mm' (no spaces) → height='880'; raw_text retains original."""
        html = _html_dim_table(
            ('Height', '820-880mm'), ('Width', '597mm'), ('Depth', '574mm')
        )
        with patch('adapters.haier.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '880'
        assert result.width == '597'
        assert result.depth == '574'
        assert '820' in result.raw_text   # original range preserved for audit

    def test_table_range_height_spaced_hyphen(self):
        """Table height '820 - 880mm' (spaced hyphen) → height='880'."""
        html = _html_dim_table(
            ('Height', '820 - 880mm'), ('Width', '597mm'), ('Depth', '574mm')
        )
        with patch('adapters.haier.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '880'

    def test_freetext_fallback_single_values(self):
        """No table present; free-text fallback resolves from page text."""
        html = '<html><body><p>Height 820mm Width 597mm Depth 574mm</p></body></html>'
        with patch('adapters.haier.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '820'
        assert result.width == '597'
        assert result.depth == '574'

    def test_freetext_range_height_compact_hyphen(self):
        """Free-text 'Height 820-880mm' (no spaces) → height='880'."""
        html = '<html><body><p>Height 820-880mm Width 597mm Depth 574mm</p></body></html>'
        with patch('adapters.haier.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '880'
        assert result.width == '597'
        assert result.depth == '574'

    def test_fetch_timeout_returns_not_found(self):
        """fetch_url timeout → Not Found with reason preserved."""
        with patch('adapters.haier.fetch_url', return_value=_fetch_none('timeout')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert result.reason == 'timeout'

    def test_fetch_403_returns_not_found(self):
        """fetch_url HTTP 403 → Not Found with 'access refused' in reason."""
        with patch('adapters.haier.fetch_url', return_value=_fetch_none('access refused (HTTP 403)')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'access refused' in result.reason

    def test_no_dimension_data_not_found(self):
        """Page with no spec data → Not Found."""
        html = '<html><body><p>No specs here.</p></body></html>'
        with patch('adapters.haier.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_partial_dims_needs_review(self):
        """Table has H and W but no D → Needs Review with partial values."""
        html = _html_dim_table(('Height', '820mm'), ('Width', '597mm'))
        with patch('adapters.haier.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Needs Review'
        assert result.height == '820'
        assert result.width == '597'
        assert result.depth is None

    def test_packing_row_excluded_product_dims_resolved(self):
        """Packing Height row is excluded; clean product dims → Resolved."""
        html = _html_dim_table(
            ('Packing Height', '900mm'),
            ('Height', '820mm'),
            ('Width', '597mm'),
            ('Depth', '574mm'),
        )
        with patch('adapters.haier.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '820'
        assert result.width == '597'
        assert result.depth == '574'

    def test_unexpected_exception_caught(self):
        """BeautifulSoup raises RuntimeError → Not Found with 'RuntimeError' in reason."""
        with patch('adapters.haier.fetch_url', return_value=_fetch('<html></html>')):
            with patch('adapters.haier.BeautifulSoup', side_effect=RuntimeError('parser error')):
                result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'RuntimeError' in result.reason
