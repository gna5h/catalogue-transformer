"""Tests for BekoAdapter — cm→mm conversion, widget trap, exclusions, JSON-LD image."""

from __future__ import annotations

import json
from unittest.mock import patch

from adapters.beko import BekoAdapter
from adapters.base import DimensionResult

URL = 'https://www.beko.com/nz-en/home-appliances/freestanding-dishwasher/test-bdfb9999x'

adapter = BekoAdapter()


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _html_spec(*rows: tuple[str, str]) -> str:
    """Build a Beko spec page with PropertyTable rows (label + span[role=textbox] value)."""
    items = ''.join(
        f'<div class="PropertyTable__row">'
        f'<div class="att_name PropertyTable__column">'
        f'<label class="label" for="{label}" id="{label}label">{label}</label>'
        f'<input hidden="" id="{label}" name="feature" type="text"/>'
        f'</div>'
        f'<div class="att_value PropertyTable__column">'
        f'<span aria-labelledby="{label}label" role="textbox"> {value} </span>'
        f'</div>'
        f'</div>'
        for label, value in rows
    )
    return f'<html><body><div class="PropertyTable">{items}</div></body></html>'


def _html_widget_only() -> str:
    """Build a page with ONLY the JS dimension widget — all values are placeholder zeros."""
    return (
        '<html><body>'
        '<div class="MainSpecs__items">'
        '<div class="DimensionsItem__root">'
        '<span class="JS-aos-counter DimensionsItem__number" data-counter-to="85">0</span>'
        '<span class="DimensionsItem__title">Height</span>'
        '</div>'
        '<div class="DimensionsItem__root">'
        '<span class="JS-aos-counter DimensionsItem__number" data-counter-to="59.8">0</span>'
        '<span class="DimensionsItem__title">Width</span>'
        '</div>'
        '<div class="DimensionsItem__root">'
        '<span class="JS-aos-counter DimensionsItem__number" data-counter-to="60">0</span>'
        '<span class="DimensionsItem__title">Depth</span>'
        '</div>'
        '</div>'
        '</body></html>'
    )


def _html_with_jsonld(image_url: str) -> str:
    """Build a page containing a JSON-LD Product schema with the given image URL."""
    schema = json.dumps({
        '@context': 'https://schema.org/',
        '@type': 'Product',
        'name': 'TEST',
        'image': image_url,
    })
    return (
        f'<html><head>'
        f'<script type="application/ld+json">{schema}</script>'
        f'</head><body></body></html>'
    )


def _fetch(html: str) -> tuple[str, str]:
    return (html, '')


def _fetch_none(reason: str) -> tuple[None, str]:
    return (None, reason)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestBekoAdapterParsing:

    def test_cm_to_mm_conversion_resolved(self):
        """85 cm → 850 mm, 59.8 cm → 598 mm, 60 cm → 600 mm; all Resolved."""
        html = _html_spec(
            ('Height', '85 cm'),
            ('Width',  '59.8 cm'),
            ('Depth',  '60 cm'),
        )
        with patch('adapters.beko.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '850'
        assert result.width  == '598'
        assert result.depth  == '600'

    def test_widget_zeros_not_used(self):
        """JS widget placeholder zeros must NOT produce height=0/width=0/depth=0."""
        html = _html_widget_only()
        with patch('adapters.beko.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert result.height != '0'
        assert result.width  != '0'
        assert result.depth  != '0'

    def test_packaged_rows_excluded(self):
        """Packaged Height/Width/Depth rows excluded; real dims used → Resolved."""
        html = _html_spec(
            ('Height',           '85 cm'),
            ('Width',            '59.8 cm'),
            ('Depth',            '60 cm'),
            ('Weight',           '49.3 kg'),
            ('Packaged Height',  '89.7 cm'),
            ('Packaged Width',   '65.7 cm'),
            ('Packaged Depth',   '67.4 cm'),
        )
        with patch('adapters.beko.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '850'
        assert result.width  == '598'
        assert result.depth  == '600'

    def test_mm_values_accepted(self):
        """Values already in mm are accepted without conversion."""
        html = _html_spec(
            ('Height', '850 mm'),
            ('Width',  '598 mm'),
            ('Depth',  '600 mm'),
        )
        with patch('adapters.beko.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '850'
        assert result.width  == '598'
        assert result.depth  == '600'

    def test_partial_dims_needs_review(self):
        """Only Height and Width present (no Depth) → Needs Review; depth is None."""
        html = _html_spec(
            ('Height', '85 cm'),
            ('Width',  '59.8 cm'),
        )
        with patch('adapters.beko.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Needs Review'
        assert result.height == '850'
        assert result.width  == '598'
        assert result.depth  is None

    def test_plausibility_low_height_needs_review(self):
        """Cooktop height 5.1 cm → 51 mm, below 200 mm floor → Needs Review."""
        html = _html_spec(
            ('Height', '5.1 cm'),
            ('Width',  '59 cm'),
            ('Depth',  '52 cm'),
        )
        with patch('adapters.beko.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Needs Review'
        assert result.height == '51'
        assert '51' in result.reason

    def test_no_dimension_data_not_found(self):
        """Page with no PropertyTable rows → Not Found."""
        html = '<html><body><p>No specs here.</p></body></html>'
        with patch('adapters.beko.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_fetch_timeout_returns_not_found(self):
        """fetch_url timeout → Not Found with reason preserved."""
        with patch('adapters.beko.fetch_url', return_value=_fetch_none('timeout')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert result.reason == 'timeout'

    def test_fetch_403_returns_not_found(self):
        """fetch_url HTTP 403 → Not Found with 'access refused' in reason."""
        with patch('adapters.beko.fetch_url',
                   return_value=_fetch_none('access refused (HTTP 403)')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'access refused' in result.reason

    def test_unexpected_exception_caught(self):
        """BeautifulSoup raises RuntimeError → Not Found with 'RuntimeError' in reason."""
        with patch('adapters.beko.fetch_url', return_value=_fetch('<html></html>')):
            with patch('adapters.beko.BeautifulSoup',
                       side_effect=RuntimeError('parser error')):
                result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'RuntimeError' in result.reason


class TestBekoAdapterImageExtraction:

    def test_jsonld_image_url_extracted(self):
        """JSON-LD Product.image URL is returned by get_image_url."""
        img = 'https://www.beko.com/content/dam/nz-aem/product-images/BDFB1630X/LO1.png'
        html = _html_with_jsonld(img)
        result = adapter.get_image_url(html, URL)
        assert result == img

    def test_non_product_jsonld_skipped(self):
        """JSON-LD with @type != Product is skipped; returns None."""
        schema = json.dumps({'@context': 'https://schema.org/', '@type': 'Organization'})
        html = f'<html><head><script type="application/ld+json">{schema}</script></head><body></body></html>'
        result = adapter.get_image_url(html, URL)
        assert result is None

    def test_no_jsonld_returns_none(self):
        """Page with no JSON-LD script → get_image_url returns None."""
        html = '<html><body><p>No metadata here.</p></body></html>'
        result = adapter.get_image_url(html, URL)
        assert result is None
