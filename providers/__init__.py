from .base import PropertyDataProvider, ListingSuggestion, ListingDetail
from .demo import DemoProvider

__all__ = [
    "PropertyDataProvider",
    "ListingSuggestion",
    "ListingDetail",
    "DemoProvider",
    "get_provider",
]


def get_provider():
    """Return DemoProvider unless PROPERTY_DATA_API_KEY is set (stub for RentCast/ATTOM)."""
    import os
    from .stub import LivePropertyDataProvider

    key = os.environ.get("PROPERTY_DATA_API_KEY", "").strip()
    if key:
        return LivePropertyDataProvider(api_key=key)
    return DemoProvider()
