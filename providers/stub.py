"""Live provider stub for RentCast / ATTOM / MLS IDX — falls back to Demo when unimplemented."""
from __future__ import annotations

from typing import Optional

from .base import ListingDetail, ListingSuggestion, PropertyDataProvider
from .demo import DemoProvider


class LivePropertyDataProvider(PropertyDataProvider):
    """
    Ready for PROPERTY_DATA_API_KEY (RentCast / ATTOM / MLS IDX).
    Until wired, delegates to DemoProvider so the product still demos cleanly.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._fallback = DemoProvider()
        # Future: configure HTTP client, base URL from env PROPERTY_DATA_BASE_URL

    def suggest(self, q: str, limit: int = 8) -> list[ListingSuggestion]:
        # TODO: call RentCast / ATTOM autocomplete with self.api_key
        return self._fallback.suggest(q, limit=limit)

    def lookup(self, *, address: Optional[str] = None, id: Optional[str] = None) -> Optional[ListingDetail]:
        # TODO: call property detail API; map tax/insurance/HOA fields
        detail = self._fallback.lookup(address=address, id=id)
        if detail:
            detail.source = "live-stub+demo"
            detail.notes = (detail.notes or "") + " (API key present; live mapping not yet wired)"
        return detail
