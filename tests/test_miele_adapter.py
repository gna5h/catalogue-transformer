"""Tests for MieleAdapter — all dimension label variants and image extraction."""

from __future__ import annotations

from unittest.mock import patch

from adapters.miele import MieleAdapter
from adapters.base import DimensionResult

URL = 'https://shop.miele.co.nz/en/kitchen/ovens/test-oven-zid99999999/'

adapter = MieleAdapter()


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _html_spec(*rows: tuple[str, str]) -> str:
    """Build a Miele spec page with ish-ca-type/ish-ca-value dl rows."""
    items = ''.join(
        f'<dl class="attribute-list-item">'
        f'<dt class="ish-ca-type">{label}</dt>'
        f'<dd class="ish-ca-value">{value}</dd>'
        f'<dd style="clear:both;"></dd>'
        f'</dl>'
        for label, value in rows
    )
    return f'<html><body><div class="group-body collapse">{items}</div></body></html>'


def _html_gallery(*imgs: tuple[str, str]) -> str:
    """Build a page with gallery images. Each entry is (src, alt)."""
    items = ''.join(
        f'<div class="item"><img src="{src}" alt="{alt}"></div>'
        for src, alt in imgs
    )
    return f'<html><body><div class="product-images">{items}</div></body></html>'


def _fetch(html: str) -> tuple[str, str]:
    return (html, '')


def _fetch_none(reason: str) -> tuple[None, str]:
    return (None, reason)


# ---------------------------------------------------------------------------
# Dimension parsing tests
# ---------------------------------------------------------------------------


class TestMieleAdapterDimensions:

    def test_combined_w_h_d_label_resolved(self):
        """'Appliance dimensions (W x H x D) in mm' → '520 x 305 x 422' → Resolved."""
        html = _html_spec(
            ('Appliance dimensions (W x H x D) in mm', '520 x 305 x 422'),
        )
        with patch('adapters.miele.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.width  == '520'
        assert result.height == '305'
        assert result.depth  == '422'
        assert '520 x 305 x 422' in result.raw_text

    def test_combined_h_w_d_label_cooktop_needs_review(self):
        """Cooktop 'Dimensions (H x W x D) in mm' → '53 x 620 x 520' → Needs Review (h < 200)."""
        html = _html_spec(
            ('Dimensions (H x W x D) in mm', '53 x 620 x 520'),
        )
        with patch('adapters.miele.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Needs Review'
        assert result.height == '53'
        assert result.width  == '620'
        assert result.depth  == '520'
        assert '53' in result.reason

    def test_cutout_labels_excluded_appliance_dims_used(self):
        """Cut-out and internal cutout labels excluded; Appliance dimensions row Resolved."""
        html = _html_spec(
            ('Appliance dimensions (W x H x D) in mm', '595 x 596 x 568'),
            ('Cut-out dimensions (W x D) with surface-mounted installation in mm', '560 x 500'),
            ('Cutout dimensions in mm (width) with surface-mounted installation', '560'),
            ('Internal cutout dimensions in mm (width) with flush installation', '600'),
        )
        with patch('adapters.miele.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '596'
        assert result.width  == '595'
        assert result.depth  == '568'

    def test_niche_labels_only_not_found(self):
        """Only niche labels present (integrated product) → Not Found."""
        html = _html_spec(
            ('Niche width minimal in mm', '600'),
            ('Niche height minimal in mm', '845'),
            ('Niche depth in mm', '600'),
        )
        with patch('adapters.miele.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_individual_appliance_labels_resolved(self):
        """'Appliance height/width/depth in mm' (dishwasher pattern) → Resolved."""
        html = _html_spec(
            ('Niche width minimal in mm',  '600'),
            ('Appliance width in mm',       '598'),
            ('Appliance height in mm',      '845'),
            ('Appliance depth in mm',       '600'),
            ('Depth with door open in cm',  '119'),
        )
        with patch('adapters.miele.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '845'
        assert result.width  == '598'
        assert result.depth  == '600'

    def test_individual_dimension_mm_labels_resolved(self):
        """'Dimensions in mm (height/width/depth)' fallback (laundry) → Resolved."""
        html = _html_spec(
            ('Dimensions in mm (height)', '850'),
            ('Dimensions in mm (width)',  '596'),
            ('Dimensions in mm (depth)',  '698'),
        )
        with patch('adapters.miele.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '850'
        assert result.width  == '596'
        assert result.depth  == '698'

    def test_combined_takes_priority_over_individual(self):
        """Combined label found first; individual axis labels are not used."""
        html = _html_spec(
            ('Dimensions (H x W x D) in mm', '850 x 596 x 698'),
            ('Dimensions in mm (height)', '999'),
            ('Dimensions in mm (width)',  '999'),
            ('Dimensions in mm (depth)',  '999'),
        )
        with patch('adapters.miele.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.height == '850'
        assert result.width  == '596'
        assert result.depth  == '698'

    def test_no_dimension_data_not_found(self):
        """Page with no spec rows → Not Found."""
        html = '<html><body><p>No specs here.</p></body></html>'
        with patch('adapters.miele.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_fetch_timeout_returns_not_found(self):
        """fetch_url timeout → Not Found with reason preserved."""
        with patch('adapters.miele.fetch_url', return_value=_fetch_none('timeout')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert result.reason == 'timeout'

    def test_fetch_403_returns_not_found(self):
        """fetch_url HTTP 403 → Not Found with 'access refused' in reason."""
        with patch('adapters.miele.fetch_url',
                   return_value=_fetch_none('access refused (HTTP 403)')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'access refused' in result.reason

    def test_unexpected_exception_caught(self):
        """BeautifulSoup raises RuntimeError → Not Found with 'RuntimeError' in reason."""
        with patch('adapters.miele.fetch_url', return_value=_fetch('<html></html>')):
            with patch('adapters.miele.BeautifulSoup',
                       side_effect=RuntimeError('parser error')):
                result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'RuntimeError' in result.reason


# ---------------------------------------------------------------------------
# Image extraction tests
# ---------------------------------------------------------------------------


class TestMieleAdapterImageExtraction:

    def test_plain_product_photo_selected(self):
        """First img with alt ending 'product photo' (no view suffix) is returned."""
        html = _html_gallery(
            ('https://media.miele.com/images/1/2/3.png?d=500&impolicy=z-boxed',
             'M 6012 SC product photo'),
            ('https://media.miele.com/images/1/2/4.png?d=500&impolicy=z-boxed',
             'M 6012 SC product photo, Laydowns Back View'),
        )
        img_url = adapter.get_image_url(html, URL)
        assert img_url == 'https://media.miele.com/images/1/2/3.png?d=500&impolicy=z-boxed'

    def test_view_labeled_images_skipped(self):
        """Imgs with 'product photo, View3 L' alt are skipped; plain shot returned."""
        html = _html_gallery(
            ('https://media.miele.com/images/1/2/4.png?d=500&impolicy=z-boxed',
             'H 2861 BP product photo, View3 L'),
            ('https://media.miele.com/images/1/2/5.png?d=500&impolicy=z-boxed',
             'H 2861 BP product photo'),
        )
        img_url = adapter.get_image_url(html, URL)
        assert img_url == 'https://media.miele.com/images/1/2/5.png?d=500&impolicy=z-boxed'

    def test_thumbnail_d110_skipped(self):
        """Img with d=110 (thumbnail) is skipped; d=500 version returned."""
        html = _html_gallery(
            ('https://media.miele.com/images/1/2/3.png?d=110&impolicy=z-boxed',
             'Product product photo'),
            ('https://media.miele.com/images/1/2/3.png?d=500&impolicy=z-boxed',
             'Product product photo'),
        )
        img_url = adapter.get_image_url(html, URL)
        assert img_url == 'https://media.miele.com/images/1/2/3.png?d=500&impolicy=z-boxed'

    def test_no_miele_images_returns_none(self):
        """Page with no media.miele.com images → get_image_url returns None."""
        html = '<html><body><img src="/static/logo.png" alt="logo"></body></html>'
        img_url = adapter.get_image_url(html, URL)
        assert img_url is None
