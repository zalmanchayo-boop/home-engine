"""Seeded realistic NYC / Tri-state listings for typeahead + autofill demos."""
from __future__ import annotations

from typing import Optional

from .base import ListingDetail, ListingSuggestion, PropertyDataProvider

# Plausible sample inventory — enough variety for Eastern / Brooklyn / Park Slope / etc.
DEMO_LISTINGS: list[dict] = [
    {
        "id": "demo-001",
        "address": "742 Eastern Pkwy",
        "city": "Brooklyn",
        "state": "NY",
        "zip": "11213",
        "price": 875000,
        "taxAnnual": 8400,
        "insuranceAnnual": 2800,
        "hoaMonthly": 0,
        "beds": 3,
        "baths": 2,
        "sqft": 1680,
        "notes": "Crown Heights brownstone-style",
    },
    {
        "id": "demo-002",
        "address": "318 Eastern Parkway",
        "city": "Brooklyn",
        "state": "NY",
        "zip": "11225",
        "price": 1125000,
        "taxAnnual": 9800,
        "insuranceAnnual": 3200,
        "hoaMonthly": 0,
        "beds": 4,
        "baths": 2.5,
        "sqft": 2100,
        "notes": "Near Prospect Park",
    },
    {
        "id": "demo-003",
        "address": "186 Park Place",
        "city": "Brooklyn",
        "state": "NY",
        "zip": "11217",
        "price": 1895000,
        "taxAnnual": 14200,
        "insuranceAnnual": 4100,
        "hoaMonthly": 0,
        "beds": 4,
        "baths": 3,
        "sqft": 2450,
        "notes": "Park Slope townhouse",
    },
    {
        "id": "demo-004",
        "address": "55 Park Slope Ave",
        "city": "Brooklyn",
        "state": "NY",
        "zip": "11215",
        "price": 1450000,
        "taxAnnual": 11800,
        "insuranceAnnual": 3600,
        "hoaMonthly": 425,
        "beds": 3,
        "baths": 2,
        "sqft": 1550,
        "notes": "Park Slope co-op with HOA",
    },
    {
        "id": "demo-005",
        "address": "210 West 15th Street Apt 4B",
        "city": "New York",
        "state": "NY",
        "zip": "10011",
        "price": 1295000,
        "taxAnnual": 0,
        "insuranceAnnual": 1800,
        "hoaMonthly": 985,
        "beds": 2,
        "baths": 1.5,
        "sqft": 980,
        "notes": "Chelsea condo; taxes in common charges",
    },
    {
        "id": "demo-006",
        "address": "88 Greenwich Street Apt 12C",
        "city": "New York",
        "state": "NY",
        "zip": "10006",
        "price": 975000,
        "taxAnnual": 6200,
        "insuranceAnnual": 2100,
        "hoaMonthly": 1120,
        "beds": 1,
        "baths": 1,
        "sqft": 720,
        "notes": "Financial District condo",
    },
    {
        "id": "demo-007",
        "address": "412 Bergen Street",
        "city": "Brooklyn",
        "state": "NY",
        "zip": "11217",
        "price": 1650000,
        "taxAnnual": 12600,
        "insuranceAnnual": 3900,
        "hoaMonthly": 0,
        "beds": 3,
        "baths": 2.5,
        "sqft": 1980,
        "notes": "Boerum Hill",
    },
    {
        "id": "demo-008",
        "address": "27 Willow Avenue",
        "city": "Hoboken",
        "state": "NJ",
        "zip": "07030",
        "price": 925000,
        "taxAnnual": 11200,
        "insuranceAnnual": 2400,
        "hoaMonthly": 0,
        "beds": 3,
        "baths": 2,
        "sqft": 1600,
        "notes": "Hoboken walk-up",
    },
    {
        "id": "demo-009",
        "address": "150 River Road Apt 6A",
        "city": "Edgewater",
        "state": "NJ",
        "zip": "07020",
        "price": 685000,
        "taxAnnual": 9800,
        "insuranceAnnual": 1950,
        "hoaMonthly": 540,
        "beds": 2,
        "baths": 2,
        "sqft": 1120,
        "notes": "Hudson waterfront condo",
    },
    {
        "id": "demo-010",
        "address": "903 Bedford Avenue",
        "city": "Brooklyn",
        "state": "NY",
        "zip": "11205",
        "price": 1350000,
        "taxAnnual": 10500,
        "insuranceAnnual": 3400,
        "hoaMonthly": 0,
        "beds": 4,
        "baths": 2,
        "sqft": 2050,
        "notes": "Bedford-Stuyvesant",
    },
    {
        "id": "demo-011",
        "address": "14 Court Street",
        "city": "Brooklyn",
        "state": "NY",
        "zip": "11241",
        "price": 795000,
        "taxAnnual": 5400,
        "insuranceAnnual": 2200,
        "hoaMonthly": 780,
        "beds": 2,
        "baths": 2,
        "sqft": 1050,
        "notes": "Downtown Brooklyn condo",
    },
    {
        "id": "demo-012",
        "address": "221 East 71st Street Apt 3F",
        "city": "New York",
        "state": "NY",
        "zip": "10021",
        "price": 825000,
        "taxAnnual": 0,
        "insuranceAnnual": 1650,
        "hoaMonthly": 1425,
        "beds": 1,
        "baths": 1,
        "sqft": 650,
        "notes": "UES co-op; maintenance includes taxes",
    },
]


def _label(row: dict) -> str:
    return f"{row['address']}, {row['city']}, {row['state']} {row['zip']}"


def _to_suggestion(row: dict) -> ListingSuggestion:
    return ListingSuggestion(
        id=row["id"],
        label=_label(row),
        address=row["address"],
        city=row["city"],
        state=row["state"],
        zip=row["zip"],
    )


def _to_detail(row: dict) -> ListingDetail:
    return ListingDetail(
        source="demo",
        id=row["id"],
        address=_label(row),
        city=row["city"],
        state=row["state"],
        zip=row["zip"],
        price=row["price"],
        taxAnnual=row["taxAnnual"],
        insuranceAnnual=row["insuranceAnnual"],
        hoaMonthly=row["hoaMonthly"],
        beds=row.get("beds"),
        baths=row.get("baths"),
        sqft=row.get("sqft"),
        notes=row.get("notes"),
    )


class DemoProvider(PropertyDataProvider):
    def __init__(self, listings: Optional[list[dict]] = None):
        self.listings = listings or DEMO_LISTINGS

    def suggest(self, q: str, limit: int = 8) -> list[ListingSuggestion]:
        q = (q or "").strip().lower()
        if len(q) < 2:
            return []
        hits: list[ListingSuggestion] = []
        for row in self.listings:
            hay = " ".join(
                [
                    row["address"],
                    row["city"],
                    row["state"],
                    row["zip"],
                    row.get("notes") or "",
                    _label(row),
                ]
            ).lower()
            if q in hay:
                hits.append(_to_suggestion(row))
            if len(hits) >= limit:
                break
        return hits

    def lookup(self, *, address: Optional[str] = None, id: Optional[str] = None) -> Optional[ListingDetail]:
        if id:
            for row in self.listings:
                if row["id"] == id:
                    return _to_detail(row)
        if address:
            addr = address.strip().lower()
            for row in self.listings:
                label = _label(row).lower()
                if addr == label or addr == row["address"].lower() or addr in label:
                    return _to_detail(row)
                # fuzzy: street number + key token
                if row["address"].lower() in addr or addr in label:
                    return _to_detail(row)
        return None
