"""Unit tests for prepare_image retry, reason, and magic-byte validation."""
from io import BytesIO
from unittest.mock import patch, call

import pytest
from PIL import Image as PILImage

from adapters.base import prepare_image


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jpeg_bytes() -> bytes:
    """Return a minimal valid 1×1 JPEG as bytes."""
    buf = BytesIO()
    PILImage.new('RGB', (1, 1), color=(255, 0, 0)).save(buf, format='JPEG')
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_success_on_first_attempt():
    jpeg = _make_jpeg_bytes()
    with patch('adapters.base.fetch_url', return_value=(jpeg, '')) as mock_fetch:
        result = prepare_image('http://example.com/img.jpg')

    assert result.status == 'Embedded'
    assert result.image_bytes is not None
    mock_fetch.assert_called_once()


def test_retries_on_network_failure_succeeds_on_third():
    jpeg = _make_jpeg_bytes()
    side_effects = [
        (None, 'timeout'),
        (None, 'timeout'),
        (jpeg, ''),
    ]
    with patch('adapters.base.fetch_url', side_effect=side_effects) as mock_fetch:
        result = prepare_image('http://example.com/img.jpg')

    assert result.status == 'Embedded'
    assert mock_fetch.call_count == 3


def test_all_attempts_exhausted_returns_download_failed_with_reason():
    with patch('adapters.base.fetch_url', return_value=(None, 'timeout')) as mock_fetch:
        result = prepare_image('http://example.com/img.jpg')

    assert result.status == 'Download Failed'
    assert result.reason == 'timeout'
    assert mock_fetch.call_count == 3


def test_non_image_response_no_retry():
    html_bytes = b'<html><body>Access Denied</body></html>'
    with patch('adapters.base.fetch_url', return_value=(html_bytes, '')) as mock_fetch:
        result = prepare_image('http://example.com/img.jpg')

    assert result.status == 'Download Failed'
    assert 'non-image response' in result.reason
    mock_fetch.assert_called_once()


def test_pil_error_no_retry():
    # Valid JPEG magic bytes but truncated body — PIL will raise on open
    corrupt_jpeg = b'\xff\xd8\xff\xe0' + b'\x00' * 10
    with patch('adapters.base.fetch_url', return_value=(corrupt_jpeg, '')) as mock_fetch:
        result = prepare_image('http://example.com/img.jpg')

    assert result.status == 'Download Failed'
    # reason should contain the exception class name
    assert any(name in result.reason for name in ('UnidentifiedImageError', 'OSError', 'Error'))
    mock_fetch.assert_called_once()
