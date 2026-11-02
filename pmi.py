"""Conventional borrower-paid monthly PMI (BPMI) rate grid.

Annualized % of loan amount, fixed-rate, amortization > 20 yrs (~30-yr), non-refundable,
standard GSE coverage (97-95.01: 35%, 95-90.01: 30%, 90-85.01: 25%, 85-80.01: 12%).

Sources (public rate cards):
- Arch MI / UGC Borrower-Paid Monthly Non-Refundable Annualized BPMI Rates, eff. Feb 9, 2026
  https://mortgage.archgroup.com/wp-content/uploads/sites/4/MCUS-B0229O-UGC-Borrower-Paid-Monthly-Phase-1-620-FICO-Rate-Card.pdf
  (97/95/90 rows below taken from its "Fixed" standard-coverage rows)
- MGIC BPMI Monthly Premiums rate card (same tier structure / coverage table)
  https://www.mgic.com/-/media/mi/rates/rate-cards/71-61284-rate-card-pdf-bpmi-monthly-july-2018_archive.pdf
  https://www.mgic.com/rates/mi-premium-plans
- 85-80.01 / 12% row: representative of MGIC/Radian/Enact/Essent/National MI public cards
  (that row was not legible in the Arch extract) - treat as estimate.
Actual MI pricing varies by insurer, state, DTI, loan amount, occupancy, etc. Estimate only.
"""
BUCKETS = ["760+", "740-759", "720-739", "700-719", "680-699", "660-679", "640-659", "620-639", "<620"]
# (max LTV of band, coverage, rates by bucket in BUCKETS order)
GRID = [
    (0.97, 0.35, [0.58, 0.70, 0.87, 0.99, 1.21, 1.54, 1.65, 1.86, 2.60]),
    (0.95, 0.30, [0.38, 0.53, 0.66, 0.78, 0.96, 1.28, 1.33, 1.42, 1.99]),
    (0.90, 0.25, [0.28, 0.38, 0.46, 0.55, 0.65, 0.90, 0.91, 0.94, 1.32]),
    (0.85, 0.12, [0.19, 0.20, 0.23, 0.25, 0.28, 0.38, 0.40, 0.44, 0.62]),
]
DEFAULT_BUCKET = "720-739"  # Level 1 (no credit entered yet) -> labelled "est."

def pmi_rate(ltv: float, bucket=None) -> float:
    """Annual PMI % (e.g. 0.46) or 0.0 when LTV <= 80%. LTV > 97% priced at 97% band."""
    if ltv <= 0.80 + 1e-9:
        return 0.0
    i = BUCKETS.index(bucket) if bucket in BUCKETS else BUCKETS.index(DEFAULT_BUCKET)
    row = GRID[0]
    for r in GRID:
        if ltv <= r[0] + 1e-9:
            row = r
    return row[2][i]

def pmi_info(loan: float, price: float, bucket=None) -> dict:
    ltv = loan / price if price > 0 else 0.0
    rate = pmi_rate(ltv, bucket)
    return {"pmiRatePct": rate, "pmiMonthly": loan * rate / 100 / 12 if rate else 0.0,
            "pmiBucket": bucket if bucket in BUCKETS else DEFAULT_BUCKET, "pmiEstimated": bucket not in BUCKETS}
