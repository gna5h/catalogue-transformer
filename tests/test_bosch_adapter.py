"""Tests for BoschAdapter — Technical Overview block parsing."""

from __future__ import annotations

from unittest.mock import patch

from adapters.bosch import BoschAdapter
from adapters.base import DimensionResult

URL = 'https://www.bosch-home.co.nz/en/mkt-product/test/TEST123'

adapter = BoschAdapter()


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _html_overview(*items: tuple[str, str]) -> str:
    """Build a minimal Bosch-style page with a Technical Overview list."""
    rows = ''.join(
        f'<div data-testid="technical-overview-item">'
        f'<span>{label}</span><span>{value}</span>'
        f'</div>'
        for label, value in items
    )
    return (
        '<html><body>'
        f'<div data-testid="technical-overview-list">{rows}</div>'
        '</body></html>'
    )


def _fetch(html: str) -> tuple[str, str]:
    return (html, '')


def _fetch_none(reason: str) -> tuple[None, str]:
    return (None, reason)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestBoschAdapterParsing:

    def test_resolves_hwxd_standard_label(self):
        """'Dimensions (HxWxD)' label, NxNxN mm value → Resolved with H×W×D order."""
        html = _html_overview(
            ('Energy Star Rating', '4 Energy Star'),
            ('Dimensions (HxWxD)', '595x594x548 mm'),
        )
        with patch('adapters.bosch.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '595'
        assert result.width == '594'
        assert result.depth == '548'

    def test_resolves_hwxd_product_label(self):
        """'Dimensions of the product' label (washing machine format) → Resolved."""
        html = _html_overview(
            ('Dimensions of the product', '845x598x590 mm'),
        )
        with patch('adapters.bosch.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '845'
        assert result.width == '598'
        assert result.depth == '590'

    def test_packed_label_excluded(self):
        """'Dimensions of the packed product' label → excluded; only product dims taken."""
        html = _html_overview(
            ('Dimensions of the packed product (HxWxD)', '885x645x680 mm'),
            ('Dimensions of the product', '845x598x590 mm'),
        )
        with patch('adapters.bosch.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '845'
        assert result.width == '598'
        assert result.depth == '590'

    def test_packed_only_returns_not_found(self):
        """Only a packed-product dimension item present → excluded → Not Found."""
        html = _html_overview(
            ('Dimensions of the packed product (HxWxD)', '885x645x680 mm'),
        )
        with patch('adapters.bosch.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_no_overview_section_not_found(self):
        """Page has no technical-overview-list → Not Found."""
        html = '<html><body><p>No overview here.</p></body></html>'
        with patch('adapters.bosch.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_no_dimension_item_not_found(self):
        """Technical Overview present but no dimension item → Not Found (fridge case)."""
        html = _html_overview(
            ('Energy Star Rating', '4 Energy Star'),
            ('Freshness system', 'VitaFresh'),
        )
        with patch('adapters.bosch.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'Technical Overview' in result.reason

    def test_cooktop_height_triggers_needs_review(self):
        """Cooktop body height 51mm is outside 200–2500mm → Needs Review."""
        html = _html_overview(
            ('Dimensions (HxWxD)', '51x816x527 mm'),
        )
        with patch('adapters.bosch.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Needs Review'
        assert result.height == '51'
        assert result.width == '816'
        assert result.depth == '527'

    def test_fetch_timeout_returns_not_found(self):
        """fetch_url timeout → Not Found with reason preserved."""
        with patch('adapters.bosch.fetch_url', return_value=_fetch_none('timeout')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert result.reason == 'timeout'

    def test_fetch_403_returns_not_found(self):
        """fetch_url HTTP 403 → Not Found with 'access refused' in reason."""
        with patch('adapters.bosch.fetch_url', return_value=_fetch_none('access refused (HTTP 403)')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'access refused' in result.reason

    def test_unexpected_exception_caught(self):
        """BeautifulSoup raises RuntimeError → Not Found with 'RuntimeError' in reason."""
        with patch('adapters.bosch.fetch_url', return_value=_fetch('<html></html>')):
            with patch('adapters.bosch.BeautifulSoup', side_effect=RuntimeError('parser error')):
                result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'RuntimeError' in result.reason


# ---------------------------------------------------------------------------
# BoschAdapter.get_image_url
# ---------------------------------------------------------------------------

_SHOT_PRIMARY = 'https://media3.bsh-group.com/Product_Shots/21577869_TEST123_STP_def.webp'
_SHOT_SECONDARY = 'https://media3.bsh-group.com/Product_Shots/26376459_TEST123_def.webp'
_LINE_DRAWING = 'https://media3.bsh-group.com/Line_Drawings/17189134_Side_View.webp'
_IMAGES_PATH = 'https://media3.bsh-group.com/Images/25973719_Feature_en-NZ.webp'
_OG_IMAGE = 'https://media3.bsh-group.com/Product_Shots/og_image.webp'


def _html_jsonld(*image_urls: str, og_content: str = '') -> str:
    images_json = ', '.join(f'"{u}"' for u in image_urls)
    og_tag = f'<meta property="og:image" content="{og_content}" />' if og_content else ''
    return (
        f'<html><head>{og_tag}'
        f'<script type="application/ld+json">'
        f'{{"@type": "Product", "image": [{images_json}]}}'
        f'</script>'
        f'</head><body></body></html>'
    )


class TestBoschAdapterGetImageUrl:

    def test_jsonld_first_product_shot_returned(self):
        """JSON-LD image[0] is Product_Shots → returned as primary image."""
        html = _html_jsonld(_SHOT_PRIMARY, _SHOT_SECONDARY, _LINE_DRAWING, og_content=_OG_IMAGE)
        result = adapter.get_image_url(html, URL)
        assert result == _SHOT_PRIMARY

    def test_jsonld_skips_line_drawings_finds_product_shot(self):
        """Line_Drawings entries come before Product_Shots → skipped; Product_Shots returned."""
        html = _html_jsonld(_LINE_DRAWING, _SHOT_PRIMARY, og_content=_OG_IMAGE)
        result = adapter.get_image_url(html, URL)
        assert result == _SHOT_PRIMARY

    def test_jsonld_skips_images_path_takes_product_shots(self):
        """Images/ lifestyle entries skipped; Product_Shots returned."""
        html = _html_jsonld(_IMAGES_PATH, _SHOT_PRIMARY, og_content=_OG_IMAGE)
        result = adapter.get_image_url(html, URL)
        assert result == _SHOT_PRIMARY

    def test_no_jsonld_falls_back_to_og_image(self):
        """No JSON-LD on page → inherited og:image behaviour used."""
        html = f'<html><head><meta property="og:image" content="{_OG_IMAGE}" /></head><body></body></html>'
        result = adapter.get_image_url(html, URL)
        assert result == _OG_IMAGE

    def test_no_product_shots_in_jsonld_falls_back_to_og_image(self):
        """JSON-LD has only Line_Drawings entries → falls back to og:image."""
        html = _html_jsonld(_LINE_DRAWING, og_content=_OG_IMAGE)
        result = adapter.get_image_url(html, URL)
        assert result == _OG_IMAGE
