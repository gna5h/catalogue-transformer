"""Tests for SamsungAdapter — covers all five confirmed spec formats."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from adapters.samsung import SamsungAdapter
from adapters.base import DimensionResult

URL = 'https://www.samsung.com/nz/test/test-product/'

adapter = SamsungAdapter()


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _html_pdd32(*pairs: tuple[str, str]) -> str:
    """Build a minimal page using the current Samsung pdd32 spec list structure."""
    items = ''.join(
        f'<li class="pdd32-product-spec__content-item" role="listitem">'
        f'<p class="pdd32-product-spec__content-item-title">{label}</p>'
        f'<p class="pdd32-product-spec__content-item-desc">{value}</p>'
        f'</li>'
        for label, value in pairs
    )
    return (
        '<html><body>'
        f'<ul class="pdd32-product-spec__content-list" role="list">{items}</ul>'
        '</body></html>'
    )


def _html_dl(*pairs: tuple[str, str]) -> str:
    """Build a minimal page with a legacy <dl> containing the given dt/dd pairs."""
    items = ''.join(f'<dt>{label}</dt><dd>{value}</dd>' for label, value in pairs)
    return f'<html><body><dl>{items}</dl></body></html>'


def _html_business(*pairs: tuple[str, str]) -> str:
    """Build a minimal page using the Samsung /business/ storefront spec structure."""
    items = ''.join(
        f'<li class="spec-highlight__item" role="listitem">'
        f'<strong class="spec-highlight__title">{label}</strong>'
        f'<span class="spec-highlight__value">{value}</span>'
        f'</li>'
        for label, value in pairs
    )
    return (
        '<html><body>'
        f'<ul class="spec-highlight__list" role="list">{items}</ul>'
        '</body></html>'
    )


def _fetch(html: str) -> tuple[str, str]:
    return (html, '')


def _fetch_none(reason: str) -> tuple[None, str]:
    return (None, reason)


# ---------------------------------------------------------------------------
# Spec parsing tests (pdd32 structure — current live Samsung page format)
# ---------------------------------------------------------------------------


class TestSamsungAdapterParsing:

    def test_dishwasher_individual_net_labels_resolved(self):
        """Net Width / Net Height / Net Depth separate labels (dishwasher) → Resolved."""
        html = _html_pdd32(
            ('Net Width', '598 mm'),
            ('Net Height', '845 mm'),
            ('Net Depth', '600 mm'),
            ('Gross Width', '655 mm'),
            ('Gross Height', '875 mm'),
        )
        with patch('adapters.samsung.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.width  == '598'
        assert result.height == '845'
        assert result.depth  == '600'

    def test_washer_combined_net_dimension_wxhxd_resolved(self):
        """'Net Dimension (WxHxD): W x H x D mm' (washing machine) → Resolved, W×H×D order."""
        html = _html_pdd32(
            ('Net Dimension (WxHxD)', '600 x 850 x 595 mm'),
            ('Gross Dimension (WxHxD)', '670 x 890 x 660 mm'),
        )
        with patch('adapters.samsung.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.width  == '600'
        assert result.height == '850'
        assert result.depth  == '595'

    def test_cooktop_combined_net_wxhxd_height_out_of_range_needs_review(self):
        """'Net (WxHxD)' (cooktop) with H=44mm → Needs Review (below 200mm plausibility)."""
        html = _html_pdd32(
            ('Net (WxHxD)', '590 X 44 X 520 mm'),
            ('Package (WxHxD)', '690 x 108 x 645 mm'),
        )
        with patch('adapters.samsung.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Needs Review'
        assert result.width  == '590'
        assert result.height == '44'
        assert result.depth  == '520'
        assert '44' in result.reason

    def test_oven_outside_wxhxd_resolved(self):
        """'Outside (WxHxD)' label (oven) → Resolved; Cavity row excluded."""
        html = _html_pdd32(
            ('Outside (WxHxD)', '595 x 596 x 550 mm'),
            ('Cavity (WxHxD)', '560 x 579 x 549 mm'),
        )
        with patch('adapters.samsung.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.width  == '595'
        assert result.height == '596'
        assert result.depth  == '550'

    def test_fridge_multiple_height_variants_first_wins(self):
        """Fridge with 'Net Case Height with Hinge(mm)' then 'without Hinge' → first wins."""
        html = _html_pdd32(
            ('Net Case Height with Hinge(mm)', '1779 mm'),
            ('Net Case Height without Hinge(mm)', '1748 mm'),
            ('Net Depth with Door Handle(mm)', '723 mm'),
            ('Net Depth without Door Handle(mm)', '723 mm'),
            ('Net Width(mm)', '912 mm'),
        )
        with patch('adapters.samsung.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.height == '1779'
        assert result.depth  == '723'
        assert result.width  == '912'

    def test_gross_labels_excluded(self):
        """Gross Width/Height/Depth labels are excluded; only Net values used."""
        html = _html_pdd32(
            ('Gross Width', '655 mm'),
            ('Gross Height', '875 mm'),
            ('Gross Depth', '645 mm'),
        )
        with patch('adapters.samsung.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_package_dimension_excluded(self):
        """Package (WxHxD) label is excluded from combined format detection."""
        html = _html_pdd32(
            ('Package (WxHxD)', '690 x 108 x 645 mm'),
            ('Net (WxHxD)', '590 X 44 X 520 mm'),
        )
        with patch('adapters.samsung.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        # Package excluded, Net taken → width=590, height=44, depth=520 → Needs Review (H<200)
        assert result.width  == '590'
        assert result.height == '44'
        assert result.depth  == '520'

    def test_no_dimension_data_not_found(self):
        """Page with no spec data → Not Found."""
        html = '<html><body><p>No specs here.</p></body></html>'
        with patch('adapters.samsung.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'

    def test_partial_dims_needs_review(self):
        """Only Net Width and Net Height present, no Depth → Needs Review."""
        html = _html_pdd32(
            ('Net Width', '598 mm'),
            ('Net Height', '845 mm'),
        )
        with patch('adapters.samsung.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Needs Review'
        assert result.width  == '598'
        assert result.height == '845'
        assert result.depth  is None

    def test_legacy_dt_dd_structure_still_works(self):
        """Legacy <dt>/<dd> spec structure is still recognised as a fallback."""
        html = _html_dl(
            ('Net Width', '598 mm'),
            ('Net Height', '845 mm'),
            ('Net Depth', '600 mm'),
        )
        with patch('adapters.samsung.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.width  == '598'
        assert result.height == '845'
        assert result.depth  == '600'

    def test_business_storefront_individual_net_labels_resolved(self):
        """Business-storefront markup with Net Width/Height/Depth parses to Resolved."""
        html = _html_business(
            ('Net Width', '598'),
            ('Net Height', '845'),
            ('Net Depth', '600'),
        )
        with patch('adapters.samsung.fetch_url', return_value=_fetch(html)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Resolved'
        assert result.width  == '598'
        assert result.height == '845'
        assert result.depth  == '600'

    def test_fetch_timeout_returns_not_found(self):
        """fetch_url timeout → Not Found with reason preserved."""
        with patch('adapters.samsung.fetch_url', return_value=_fetch_none('timeout')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert result.reason == 'timeout'

    def test_fetch_403_returns_not_found(self):
        """fetch_url HTTP 403 → Not Found with 'access refused' in reason."""
        with patch('adapters.samsung.fetch_url', return_value=_fetch_none('access refused (HTTP 403)')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'access refused' in result.reason

    def test_fetch_404_returns_not_found(self):
        """fetch_url HTTP 404 → Not Found with 'HTTP 404' in reason."""
        with patch('adapters.samsung.fetch_url', return_value=_fetch_none('HTTP 404')):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert result.reason == 'HTTP 404'

    def test_fetch_redirect_returns_not_found(self):
        """fetch_url redirect detection → Not Found with 'redirected to' in reason."""
        reason = 'redirected to: https://www.samsung.com/nz/'
        with patch('adapters.samsung.fetch_url', return_value=_fetch_none(reason)):
            result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'redirected to' in result.reason

    def test_unexpected_exception_caught(self):
        """BeautifulSoup raises RuntimeError → Not Found with 'RuntimeError' in reason."""
        with patch('adapters.samsung.fetch_url', return_value=_fetch('<html></html>')):
            with patch('adapters.samsung.BeautifulSoup', side_effect=RuntimeError('parser error')):
                result = adapter.fetch_dimensions(URL)
        assert result.confidence == 'Not Found'
        assert 'RuntimeError' in result.reason


# ---------------------------------------------------------------------------
# TestSamsungAdapterGetImageUrl
# ---------------------------------------------------------------------------

_PRODUCT_IMG = 'https://images.samsung.com/is/image/samsung/nz-dw60bg8070sr-1.jpg'
_LOGO_IMG    = 'https://images.samsung.com/etc/designs/smg/global/imgs/logo-square-letter.png'
_BUSINESS_URL = 'https://www.samsung.com/nz/business/dishwashers/dw60bg8070sr/'
_GALLERY_IMG = (
    'https://images.samsung.com/is/image/samsung/p6pim/nz/rf71db9956qdsa/'
    'gallery/nz-t-style-french-door-32inch-family-hub-rf71db9956qdsa-550097446'
    '?$1164_776_PNG$'
)
_GALLERY_THUMB = (
    'https://images.samsung.com/is/image/samsung/p6pim/nz/rf71db9956qdsa/'
    'gallery/nz-t-style-french-door-32inch-family-hub-rf71db9956qdsa-thumb-550097447'
)


def _html_og(content: str) -> str:
    return f'<html><head><meta property="og:image" content="{content}" /></head><body></body></html>'


def _html_gallery_img(src: str, og: str = '') -> str:
    og_tag = f'<meta property="og:image" content="{og}" />' if og else ''
    return f'<html><head>{og_tag}</head><body><img src="{src}" alt="Product"/></body></html>'


class TestBusinessUrlDimensionSurvival:

    def test_business_url_dims_resolved_even_when_consumer_fetch_fails(self):
        """/business/ URL with valid dimension data: dimensions Resolved even if consumer URL fetch fails.

        This guards the fix for the overly-broad try/except that previously discarded
        resolved dimensions whenever get_image_url() (including the business→consumer
        fallback) raised or the consumer fetch failed in a way that propagated.
        """
        business_url = 'https://www.samsung.com/nz/business/dishwashers/freestanding/dw60m6055fg-sa/'
        html = _html_pdd32(
            ('Net Width',  '598 mm'),
            ('Net Height', '845 mm'),
            ('Net Depth',  '600 mm'),
        )
        # First fetch_url call returns the business page HTML (dimensions present).
        # Second fetch_url call (inside get_image_url → consumer fallback) fails → None.
        fetch_results = iter([(html, ''), (None, 'HTTP 404')])
        with patch('adapters.samsung.fetch_url', side_effect=lambda *a, **kw: next(fetch_results)):
            result = adapter.fetch_dimensions(business_url)
        assert result.confidence == 'Resolved'
        assert result.width  == '598'
        assert result.height == '845'
        assert result.depth  == '600'
        assert result.image_status == 'Not Found'  # image correctly absent, not a crash


class TestSamsungAdapterGetImageUrl:

    def test_business_url_fetches_consumer_og_image(self):
        """/business/ URL, no gallery on either page → step 3 consumer og:image returned as last resort."""
        consumer_html = _html_og(_PRODUCT_IMG)
        with patch('adapters.samsung.fetch_url', return_value=(consumer_html, '')):
            result = adapter.get_image_url('<html></html>', _BUSINESS_URL)
        assert result == _PRODUCT_IMG

    def test_business_url_consumer_fetch_fails_returns_none(self):
        """/business/ URL → consumer fetch fails → None."""
        with patch('adapters.samsung.fetch_url', return_value=(None, 'timeout')):
            result = adapter.get_image_url('<html></html>', _BUSINESS_URL)
        assert result is None

    def test_business_url_consumer_returns_logo_returns_none(self):
        """/business/ URL → consumer og:image is generic logo → None."""
        consumer_html = _html_og(_LOGO_IMG)
        with patch('adapters.samsung.fetch_url', return_value=(consumer_html, '')):
            result = adapter.get_image_url('<html></html>', _BUSINESS_URL)
        assert result is None

    def test_consumer_url_valid_og_image_returned(self):
        """Normal URL → gallery extraction finds nothing (_PRODUCT_IMG lacks p6pim path) → og:image fallback triggers."""
        html = _html_og(_PRODUCT_IMG)
        result = adapter.get_image_url(html, URL)
        assert result == _PRODUCT_IMG

    def test_consumer_url_logo_og_image_returns_none(self):
        """Normal URL → og:image is the generic logo → None."""
        html = _html_og(_LOGO_IMG)
        result = adapter.get_image_url(html, URL)
        assert result is None


# ---------------------------------------------------------------------------
# TestSamsungGalleryExtraction
# ---------------------------------------------------------------------------


class TestSamsungGalleryExtraction:

    def test_consumer_url_gallery_image_returned(self):
        """Gallery CDN img present in HTML → returned directly (no og:image consulted)."""
        html = _html_gallery_img(_GALLERY_IMG)
        result = adapter.get_image_url(html, URL)
        assert result == _GALLERY_IMG

    def test_consumer_url_gallery_thumbnail_skipped_og_image_fallback(self):
        """Only a thumbnail gallery img in page → skipped; og:image fallback used."""
        html = _html_gallery_img(_GALLERY_THUMB, og=_PRODUCT_IMG)
        result = adapter.get_image_url(html, URL)
        assert result == _PRODUCT_IMG

    def test_consumer_url_gallery_wins_over_og_image(self):
        """Both gallery img and valid og:image present → gallery img wins."""
        html = _html_gallery_img(_GALLERY_IMG, og=_PRODUCT_IMG)
        result = adapter.get_image_url(html, URL)
        assert result == _GALLERY_IMG

    def test_consumer_url_no_gallery_no_og_returns_none(self):
        """No gallery img and no og:image → None."""
        html = '<html><body><p>Nothing here</p></body></html>'
        result = adapter.get_image_url(html, URL)
        assert result is None

    def test_business_url_gallery_on_business_page_returned(self):
        """Business page has gallery img → returned immediately, consumer fetch never called."""
        html = _html_gallery_img(_GALLERY_IMG)
        result = adapter.get_image_url(html, _BUSINESS_URL)
        assert result == _GALLERY_IMG

    def test_business_url_gallery_missing_falls_back_to_consumer_gallery(self):
        """Business page has no gallery img; consumer page does → consumer gallery returned."""
        consumer_html = _html_gallery_img(_GALLERY_IMG)
        with patch('adapters.samsung.fetch_url', return_value=(consumer_html, '')):
            result = adapter.get_image_url('<html><body></body></html>', _BUSINESS_URL)
        assert result == _GALLERY_IMG

    def test_business_url_both_missing_consumer_og_fallback(self):
        """No gallery on business or consumer page; consumer has valid og:image → returned."""
        consumer_html = _html_og(_PRODUCT_IMG)
        with patch('adapters.samsung.fetch_url', return_value=(consumer_html, '')):
            result = adapter.get_image_url('<html><body></body></html>', _BUSINESS_URL)
        assert result == _PRODUCT_IMG
