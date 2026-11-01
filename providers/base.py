"""Pluggable property-data provider interface (RentCast / ATTOM / MLS IDX ready)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class ListingSuggestion:
    id: str
    label: str
    address: str
    city: str
    state: str
    zip: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ListingDetail:
    source: str
    address: str
    price: float
    taxAnnual: float
    insuranceAnnual: float
    hoaMonthly: float
    beds: Optional[float] = None
    baths: Optional[float] = None
    sqft: Optional[int] = None
    notes: Optional[str] = None
    id: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


class PropertyDataProvider(ABC):
    """Stub interface for listing suggest + lookup. Do not scrape portals here."""

    @abstractmethod
    def suggest(self, q: str, limit: int = 8) -> list[ListingSuggestion]:
        ...

    @abstractmethod
    def lookup(self, *, address: Optional[str] = None, id: Optional[str] = None) -> Optional[ListingDetail]:
        ...
