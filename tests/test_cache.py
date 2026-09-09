"""Cache-layer tests: version staleness, force_retry, and dim/img independence."""

from __future__ import annotations

import pytest

import cache
from adapters.base import DimensionResult

URL = 'https://www.samsung.com/nz/test/product/'

_RESOLVED = DimensionResult(
    height='845', width='598', depth='600',
    confidence='Resolved',
    source_url=URL,
)

_EMBEDDED = DimensionResult(
    height='845', width='598', depth='600',
    confidence='Resolved',
    source_url=URL,
    image_bytes=b'\xff\xd8\xff',
    image_status='Embedded',
)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Redirect all cache I/O to a per-test temporary database."""
    db_path = tmp_path / 'cache' / 'dimension_cache.db'
    monkeypatch.setattr(cache, '_DB_PATH', db_path)


def _force_dim_version(stored_v: int) -> None:
    """Write a specific dim_version into the cached row for URL."""
    conn = cache._connect()
    conn.execute('UPDATE dim_cache SET dim_version = ? WHERE url = ?',
                 (stored_v, cache._normalise_url(URL)))
    conn.commit()
    conn.close()


def _force_img_version(stored_v: int) -> None:
    """Write a specific img_version into the cached row for URL."""
    conn = cache._connect()
    conn.execute('UPDATE dim_cache SET img_version = ? WHERE url = ?',
                 (stored_v, cache._normalise_url(URL)))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# should_fetch_dims — version staleness
# ---------------------------------------------------------------------------

class TestShouldFetchDimsVersionStaleness:

    def test_not_in_cache_always_fetches(self):
        assert cache.should_fetch_dims(URL, force_retry=False, brand='Samsung') is True

    def test_resolved_current_version_not_refetched(self):
        cache.store_result(URL, _RESOLVED, brand='Samsung')
        assert cache.should_fetch_dims(URL, force_retry=False, brand='Samsung') is False

    def test_resolved_stale_dim_version_refetched(self):
        cache.store_result(URL, _RESOLVED, brand='Samsung')
        current_v = cache.ADAPTER_VERSIONS['Samsung']['dim']
        _force_dim_version(current_v - 1)
        assert cache.should_fetch_dims(URL, force_retry=False, brand='Samsung') is True

    def test_non_resolved_always_refetched(self):
        not_found = DimensionResult(
            confidence='Not Found', source_url=URL, reason='No dimension data found on page'
        )
        cache.store_result(URL, not_found, brand='Samsung')
        assert cache.should_fetch_dims(URL, force_retry=False, brand='Samsung') is True

    def test_null_stored_version_treated_as_one(self):
        """Legacy rows stored before versioning (NULL dim_version) default to v1."""
        cache.store_result(URL, _RESOLVED, brand='Samsung')
        conn = cache._connect()
        conn.execute('UPDATE dim_cache SET dim_version = NULL WHERE url = ?',
                     (cache._normalise_url(URL),))
        conn.commit()
        conn.close()
        # Samsung dim version is 3, so NULL (→1) is stale → should fetch
        assert cache.should_fetch_dims(URL, force_retry=False, brand='Samsung') is True


# ---------------------------------------------------------------------------
# should_fetch_dims — force_retry
# ---------------------------------------------------------------------------

class TestForceRetry:

    def test_force_retry_false_respects_resolved_cache(self):
        cache.store_result(URL, _RESOLVED, brand='Samsung')
        assert cache.should_fetch_dims(URL, force_retry=False, brand='Samsung') is False

    def test_force_retry_true_overrides_resolved_cache(self):
        cache.store_result(URL, _RESOLVED, brand='Samsung')
        assert cache.should_fetch_dims(URL, force_retry=True, brand='Samsung') is True

    def test_force_retry_true_on_not_found_still_fetches(self):
        """force_retry=True on a non-Resolved row — still fetches (non-Resolved always does)."""
        not_found = DimensionResult(confidence='Not Found', source_url=URL, reason='timeout')
        cache.store_result(URL, not_found, brand='Samsung')
        assert cache.should_fetch_dims(URL, force_retry=True, brand='Samsung') is True


# ---------------------------------------------------------------------------
# should_fetch_image — version staleness
# ---------------------------------------------------------------------------

class TestShouldFetchImageVersionStaleness:

    def test_not_in_cache_always_fetches(self):
        assert cache.should_fetch_image(URL, brand='Samsung') is True

    def test_embedded_current_version_not_refetched(self):
        cache.store_result(URL, _EMBEDDED, brand='Samsung')
        assert cache.should_fetch_image(URL, brand='Samsung') is False

    def test_embedded_stale_img_version_refetched(self):
        cache.store_result(URL, _EMBEDDED, brand='Samsung')
        current_v = cache.ADAPTER_VERSIONS['Samsung']['img']
        _force_img_version(current_v - 1)
        assert cache.should_fetch_image(URL, brand='Samsung') is True

    def test_not_found_image_status_always_refetched(self):
        cache.store_result(URL, _RESOLVED, brand='Samsung')  # image_status='Not Found'
        assert cache.should_fetch_image(URL, brand='Samsung') is True


# ---------------------------------------------------------------------------
# Dim / img version independence
# ---------------------------------------------------------------------------

class TestDimImgVersionIndependence:

    def test_stale_dim_does_not_trigger_img_refetch(self):
        """Bumping dim_version alone must not cause an image re-fetch."""
        cache.store_result(URL, _EMBEDDED, brand='Samsung')
        current_v = cache.ADAPTER_VERSIONS['Samsung']['dim']
        _force_dim_version(current_v - 1)
        # dim is stale → needs fetch
        assert cache.should_fetch_dims(URL, force_retry=False, brand='Samsung') is True
        # img version unchanged → still current → no re-fetch
        assert cache.should_fetch_image(URL, brand='Samsung') is False

    def test_stale_img_does_not_trigger_dim_refetch(self):
        """Bumping img_version alone must not cause a dim re-fetch."""
        cache.store_result(URL, _EMBEDDED, brand='Samsung')
        current_v = cache.ADAPTER_VERSIONS['Samsung']['img']
        _force_img_version(current_v - 1)
        # dim version unchanged → still Resolved + current → no re-fetch
        assert cache.should_fetch_dims(URL, force_retry=False, brand='Samsung') is False
        # img is stale → needs fetch
        assert cache.should_fetch_image(URL, brand='Samsung') is True
