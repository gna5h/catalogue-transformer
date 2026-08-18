"""Brand name → adapter instance registry.

To add a new adapter:
  1. Create src/adapters/<brand>.py implementing BaseAdapter.fetch_dimensions
  2. Import and add it to ADAPTERS below.
"""

from .base import BaseAdapter, DimensionResult
from .lg import LGAdapter
from .bosch import BoschAdapter
from .samsung import SamsungAdapter

ADAPTERS: dict[str, BaseAdapter] = {
    'lg':       LGAdapter(),
    'bosch':    BoschAdapter(),
    'samsung':  SamsungAdapter(),
}


def get_adapter(brand: str) -> BaseAdapter | None:
    """Return the adapter for the given brand name (case-insensitive), or None."""
    return ADAPTERS.get(brand.strip().lower())


def fetch_for_brand(brand: str, product_url: str) -> DimensionResult:
    """
    Convenience wrapper: look up the adapter and call fetch_dimensions.
    Returns a 'Not Found' result if no adapter exists for the brand.
    """
    adapter = get_adapter(brand)
    if adapter is None:
        return DimensionResult(
            confidence='Not Found',
            source_url=product_url,
            reason=f"No adapter implemented for brand '{brand}'",
        )
    return adapter.fetch_dimensions(product_url)
