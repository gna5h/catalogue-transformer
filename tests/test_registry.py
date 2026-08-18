"""Unit tests for adapters/registry.py.

Verifies adapter dispatch, case-insensitive lookup, and fault containment.
"""

from unittest.mock import patch, MagicMock

import pytest

from adapters.registry import get_adapter, fetch_for_brand
from adapters.lg import LGAdapter
from adapters.base import DimensionResult

_URL = 'http://lg.com/nz/example-product'


class TestGetAdapter:

    def test_lg_uppercase_returns_lg_adapter(self):
        adapter = get_adapter('LG')
        assert isinstance(adapter, LGAdapter)

    def test_lg_lowercase_returns_lg_adapter(self):
        adapter = get_adapter('lg')
        assert isinstance(adapter, LGAdapter)

    def test_unknown_brand_returns_none(self):
        adapter = get_adapter('UnknownBrand')
        assert adapter is None


class TestFetchForBrand:

    def test_unknown_brand_returns_not_found_no_exception(self):
        result = fetch_for_brand('UnknownBrand', _URL)
        assert result.confidence == 'Not Found'
        assert 'UnknownBrand' in result.reason

    def test_adapter_exception_is_caught_returns_not_found(self):
        with patch.object(LGAdapter, 'fetch_dimensions', side_effect=RuntimeError('boom')):
            result = fetch_for_brand('LG', _URL)
        assert result.confidence == 'Not Found'
        assert 'RuntimeError' in result.reason
        assert result.source_url == _URL
