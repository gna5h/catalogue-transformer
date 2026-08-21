"""Tests for FPAdapter — mirrors tests/test_lg_adapter.py pattern."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from adapters.fp import FPAdapter
from adapters.base import DimensionResult, is_valid_single_url

URL = 'https://www.fisherpaykel.com/nz/test-product.html'

adapter = FPAdapter()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _html_dim_table(*rows: tuple[str, str]) -> str:
    body = ''.join(f'<tr><td>{label}</td><td>{value}</td></tr>' for label, value in rows)
    return (
        '<html><body><h2>Product dimensions</h2>'
        '<table><thead><tr><th>Attributes</th><th>Value</th></tr></thead>'
        f'<tbody>{body}</tbody></table></body></html>'
    )


def _fetch(html: str) -> tuple[str, str]:
    return (html, '')


def _fetch_none(reason: str) -> tuple[None, str]:
    return (None, reason)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestFPAdapterParsing:

    def test_table_primary_single_values(self):
        """Table is the primary source; all three axes (single mm values) → Resolved."""
        html = _html_dim_table(('Height', '820mm'), ('Width', '597mm'), ('Depth', '574mm'))
        with patch('adapters.fp.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '820'
        assert result.width == '597'
        assert result.depth == '574'

    def test_table_range_height_max_extracted(self):
        """Table height '857 - 917mm' → height='917'; raw_text retains original range."""
        html = _html_dim_table(
            ('Height', '857 - 917mm'), ('Width', '597mm'), ('Depth', '574mm')
        )
        with patch('adapters.fp.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '917'
        assert result.width == '597'
        assert result.depth == '574'
        assert '857' in result.raw_text   # original range preserved for audit

    def test_freetext_fallback_single_values(self):
        """No table present; free-text fallback resolves from page text."""
        html = '<html><body><p>Height 820mm Width 597mm Depth 574mm</p></body></html>'
        with patch('adapters.fp.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '820'
        assert result.width == '597'
        assert result.depth == '574'

    def test_freetext_range_height_max_extracted(self):
        """Free-text 'Height 857 - 917mm' → height='917' (max of range)."""
        html = '<html><body><p>Height 857 - 917mm Width 597mm Depth 574mm</p></body></html>'
        with patch('adapters.fp.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '917'
        assert result.width == '597'
        assert result.depth == '574'

    def test_fetch_timeout_returns_not_found(self):
        """fetch_url timeout → Not Found with reason preserved."""
        with patch('adapters.fp.fetch_url', return_value=_fetch_none('timeout')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert result.reason == 'timeout'

    def test_fetch_403_returns_not_found(self):
        """fetch_url HTTP 403 → Not Found with 'access refused' in reason."""
        with patch('adapters.fp.fetch_url', return_value=_fetch_none('access refused (HTTP 403)')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'access refused' in result.reason

    def test_no_dimension_data_not_found(self):
        """Page with no spec data → Not Found."""
        html = '<html><body><p>No specs here.</p></body></html>'
        with patch('adapters.fp.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_partial_dims_needs_review(self):
        """Table has H and W but no D → Needs Review with partial values."""
        html = _html_dim_table(('Height', '820mm'), ('Width', '597mm'))
        with patch('adapters.fp.fetch_url', return_value=_fetch(html)):
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
        with patch('adapters.fp.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '820'
        assert result.width == '597'
        assert result.depth == '574'

    def test_unexpected_exception_caught(self):
        """BeautifulSoup raises RuntimeError → Not Found with 'RuntimeError' in reason."""
        with patch('adapters.fp.fetch_url', return_value=_fetch('<html></html>')):
            with patch('adapters.fp.BeautifulSoup', side_effect=RuntimeError('parser error')):
                result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'RuntimeError' in result.reason


# ---------------------------------------------------------------------------
# is_valid_single_url
# ---------------------------------------------------------------------------


class TestIsValidSingleUrl:

    def test_valid_https_url_returns_true(self):
        assert is_valid_single_url('https://cdn.example.com/img.jpg') is True

    def test_valid_protocol_relative_returns_true(self):
        assert is_valid_single_url('//cdn.example.com/img.jpg') is True

    def test_demandware_root_relative_concatenated_returns_false(self):
        """Root-relative path with an embedded https:// — Demandware F&P pattern."""
        assert is_valid_single_url(
            '/on/demandware.static/-/Sites/en_NZ/https://cdn.example.com/img.jpg'
        ) is False

    def test_demandware_absolute_concatenated_returns_false(self):
        """Two https:// schemes in one value — Demandware Haier pattern."""
        assert is_valid_single_url(
            'https://site.com/on/demandware.static/https://cdn.example.com/img.jpg'
        ) is False

    def test_empty_string_returns_false(self):
        assert is_valid_single_url('') is False


# ---------------------------------------------------------------------------
# FPAdapter.get_image_url
# ---------------------------------------------------------------------------


def _html_og(content: str) -> str:
    return (
        f'<html><head>'
        f'<meta property="og:image" content="{content}" />'
        f'</head><body></body></html>'
    )


def _html_pdp(src: str, alt: str = 'DW60XT4B2, pdp') -> str:
    return (
        f'<html><head></head><body>'
        f'<img src="{src}" alt="{alt}" />'
        f'</body></html>'
    )


def _html_og_and_pdp(og_content: str, pdp_src: str) -> str:
    return (
        f'<html><head>'
        f'<meta property="og:image" content="{og_content}" />'
        f'</head><body>'
        f'<img src="{pdp_src}" alt="DW60XT4B2, pdp" />'
        f'</body></html>'
    )


class TestFPAdapterGetImageUrl:

    def test_valid_og_image_used_directly(self):
        """Well-formed og:image URL is returned without gallery fallback."""
        img_url = 'https://cdn.example.com/product.jpg'
        result = adapter.get_image_url(_html_og(img_url), URL)
        assert result == img_url

    def test_malformed_og_image_falls_back_to_pdp_gallery(self):
        """Concatenated og:image (Demandware bug) → gallery pdp img returned."""
        bad = '/on/demandware.static/-/Sites/en_NZ/https://cdn.example.com/product.jpg'
        good = 'https://cdn.example.com/product.jpg'
        result = adapter.get_image_url(_html_og_and_pdp(bad, good), URL)
        assert result == good

    def test_no_og_image_falls_back_to_pdp_gallery(self):
        """No og:image tag → gallery pdp img returned."""
        src = 'https://cdn.example.com/product.jpg'
        result = adapter.get_image_url(_html_pdp(src), URL)
        assert result == src

    def test_no_og_image_no_pdp_returns_none(self):
        """No og:image and no pdp img → None."""
        result = adapter.get_image_url('<html><body><p>No images.</p></body></html>', URL)
        assert result is None

    def test_protocol_relative_og_image_normalised(self):
        """Protocol-relative og:image is prefixed with https:."""
        result = adapter.get_image_url(_html_og('//cdn.example.com/product.jpg'), URL)
        assert result == 'https://cdn.example.com/product.jpg'
