"""Home Affordability Engine — FastAPI app with address typeahead, event log, device stats."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from providers import get_provider

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "events.db"

# Dual DTI bands: 45% = minimum required income rule; 36% = comfort band
DTI_RULE = 0.45
DTI_COMFORT = 0.36
# PMI heuristic: ~0.7% of loan amount annually when LTV > 80% (Bankrate-style mid-range)
PMI_ANNUAL_RATE = 0.007
LTV_PMI_THRESHOLD = 0.80

DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Home Affordability Engine", version="0.3.0")
provider = get_provider()

_suggest_hits: dict[str, list[float]] = {}
SUGGEST_RATE_LIMIT = 30  # per 60s window


def _init_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                type TEXT NOT NULL,
                session_id TEXT,
                address TEXT,
                device TEXT,
                user_agent TEXT,
                payload TEXT
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")


@contextmanager
def _conn():
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _log_event(
    *,
    type: str,
    session_id: Optional[str],
    address: Optional[str],
    device: Optional[str],
    user_agent: Optional[str],
    payload: Any,
) -> int:
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO events (ts, type, session_id, address, device, user_agent, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                type,
                session_id,
                address,
                device,
                (user_agent or "")[:512],
                json.dumps(payload) if payload is not None else None,
            ),
        )
        return int(cur.lastrowid)


def _ua(request: Request) -> str:
    return request.headers.get("user-agent", "")


def _rate_ok(ip: str) -> bool:
    now = time.time()
    window = _suggest_hits.setdefault(ip, [])
    _suggest_hits[ip] = [t for t in window if now - t < 60]
    if len(_suggest_hits[ip]) >= SUGGEST_RATE_LIMIT:
        return False
    _suggest_hits[ip].append(now)
    return True


def payment(principal: float, annual_rate_pct: float, years: int) -> float:
    if principal <= 0:
        return 0.0
    r = annual_rate_pct / 100.0 / 12.0
    n = years * 12
    if r == 0:
        return principal / n
    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def estimate_pmi_monthly(loan: float, price: float, pmi_override: Optional[float] = None) -> float:
    """Return monthly PMI. Auto-zero when LTV <= 80%. Override lets UI edit the amount."""
    if price <= 0 or loan <= 0:
        return 0.0
    ltv = loan / price
    if ltv <= LTV_PMI_THRESHOLD:
        return 0.0
    if pmi_override is not None:
        return max(float(pmi_override), 0.0)
    return loan * PMI_ANNUAL_RATE / 12.0


def compute_calc(data: dict[str, Any]) -> dict[str, Any]:
    price = float(data.get("price") or 0)
    down = min(float(data.get("down") or 0), price)
    rate = float(data.get("rate") or 0)
    term = int(data.get("term") or 30)
    tax = float(data.get("taxAnnual") or data.get("tax") or 0)
    ins = float(data.get("insuranceAnnual") or data.get("ins") or 0)
    hoa = float(data.get("hoaMonthly") or data.get("hoa") or 0)
    debts = float(data.get("otherMonthlyDebts") or data.get("debts") or 0)
    income = float(data.get("income") or data.get("grossMonthlyIncome") or 0)
    extra_principal = float(data.get("extraPrincipal") or 0)
    # None = auto-estimate; explicit number (incl. 0) = user override
    pmi_raw = data.get("pmiMonthly", data.get("pmi"))
    pmi_override: Optional[float]
    if pmi_raw is None or pmi_raw == "":
        pmi_override = None
    else:
        pmi_override = float(pmi_raw)

    loan = max(price - down, 0)
    ltv = (loan / price) if price > 0 else 0.0
    pi = payment(loan, rate, term)
    tax_m = tax / 12.0
    ins_m = ins / 12.0
    pmi_m = estimate_pmi_monthly(loan, price, pmi_override)
    pmi_auto = estimate_pmi_monthly(loan, price, None)
    pmi_required = ltv > LTV_PMI_THRESHOLD

    housing = pi + tax_m + ins_m + hoa + pmi_m + extra_principal
    all_obl = housing + debts
    leftover = income - all_obl
    housing_ratio = (housing / income) if income > 0 else 0.0
    dti = (all_obl / income) if income > 0 else 0.0

    # 45% = minimum required income; 36% = comfort income
    min_monthly = (housing + debts) / DTI_RULE if DTI_RULE > 0 else 0.0
    min_annual = min_monthly * 12.0
    comfort_monthly = (housing + debts) / DTI_COMFORT if DTI_COMFORT > 0 else 0.0
    comfort_annual = comfort_monthly * 12.0

    below_min = income > 0 and income < min_monthly

    # Affordable / Stretch / Stretched vs 36% and 45%
    if income <= 0:
        tone = "warn"
        label = "Add income for a full read"
        band = "unknown"
    elif dti <= DTI_COMFORT:
        tone = "good"
        label = "Affordable — at or under 36% comfort DTI"
        band = "affordable"
    elif dti <= DTI_RULE:
        tone = "warn"
        label = "Stretch — between 36% comfort and 45% max DTI"
        band = "stretch"
    else:
        tone = "bad"
        label = "Stretched — above 45% DTI minimum threshold"
        band = "stretched"

    return {
        "loan": round(loan, 2),
        "ltv": round(ltv, 4),
        "principalAndInterest": round(pi, 2),
        "taxMonthly": round(tax_m, 2),
        "insuranceMonthly": round(ins_m, 2),
        "hoaMonthly": round(hoa, 2),
        "pmiMonthly": round(pmi_m, 2),
        "pmiAuto": round(pmi_auto, 2),
        "pmiRequired": pmi_required,
        "extraPrincipal": round(extra_principal, 2),
        "housingPayment": round(housing, 2),
        "otherMonthlyDebts": round(debts, 2),
        "allObligations": round(all_obl, 2),
        "leftover": round(leftover, 2),
        "housingRatio": round(housing_ratio, 4),
        "dti": round(dti, 4),
        "dtiRule": DTI_RULE,
        "dtiComfort": DTI_COMFORT,
        "minMonthlyIncome": round(min_monthly, 2),
        "minAnnualIncome": round(min_annual, 2),
        "comfortMonthlyIncome": round(comfort_monthly, 2),
        "comfortAnnualIncome": round(comfort_annual, 2),
        "incomeBelowMinimum": below_min,
        "band": band,
        "tone": tone,
        "statusLabel": label,
        "mode": "this_home",
    }


def compute_max_price(data: dict[str, Any]) -> dict[str, Any]:
    """How-much-house mode: max price at 45% DTI and comfort price at 36% DTI."""
    income = float(data.get("income") or data.get("grossMonthlyIncome") or 0)
    debts = float(data.get("otherMonthlyDebts") or data.get("debts") or 0)
    rate = float(data.get("rate") or 6.75)
    term = int(data.get("term") or 30)
    down_pct = float(data.get("downPct") or data.get("downPercent") or 10) / 100.0
    down_pct = max(0.0, min(down_pct, 0.95))
    hoa = float(data.get("hoaMonthly") or data.get("hoa") or 0)
    # Ownership cost rates as % of home price (annual)
    tax_rate = float(data.get("taxRatePct") or 1.0) / 100.0  # default ~1%/yr
    ins_rate = float(data.get("insRatePct") or 0.35) / 100.0  # default ~0.35%/yr
    # Optional absolute annual overrides (used when provided > 0 as fixed $/yr independent of price)
    tax_fixed = float(data.get("taxAnnual") or 0)
    ins_fixed = float(data.get("insuranceAnnual") or 0)
    use_fixed_tax = tax_fixed > 0 and data.get("taxRatePct") is None
    use_fixed_ins = ins_fixed > 0 and data.get("insRatePct") is None
    pmi_annual = float(data.get("pmiAnnualRate") or (PMI_ANNUAL_RATE * 100)) / 100.0
    if data.get("pmiAnnualRate") is None:
        pmi_annual = PMI_ANNUAL_RATE

    loan_frac = 1.0 - down_pct
    # Monthly PI factor per $1 of loan
    pi_factor = payment(1.0, rate, term) if loan_frac > 0 else 0.0

    def price_at_dti(dti_target: float) -> dict[str, float]:
        budget = max(income * dti_target - debts, 0.0)
        # housing_budget = budget; housing = PI + tax_m + ins_m + hoa + pmi
        # housing = price * loan_frac * pi_factor
        #          + (tax_fixed/12 or price*tax_rate/12)
        #          + (ins_fixed/12 or price*ins_rate/12)
        #          + hoa
        #          + (price * loan_frac * pmi_annual/12 if down_pct < 0.20 else 0)
        fixed_monthly = hoa
        if use_fixed_tax:
            fixed_monthly += tax_fixed / 12.0
        if use_fixed_ins:
            fixed_monthly += ins_fixed / 12.0

        variable = loan_frac * pi_factor
        if not use_fixed_tax:
            variable += tax_rate / 12.0
        if not use_fixed_ins:
            variable += ins_rate / 12.0
        if down_pct < (1.0 - LTV_PMI_THRESHOLD):
            variable += loan_frac * pmi_annual / 12.0

        if variable <= 0:
            price = 0.0
        else:
            price = max((budget - fixed_monthly) / variable, 0.0)

        down = price * down_pct
        loan = price - down
        pi = payment(loan, rate, term)
        tax_m = (tax_fixed / 12.0) if use_fixed_tax else (price * tax_rate / 12.0)
        ins_m = (ins_fixed / 12.0) if use_fixed_ins else (price * ins_rate / 12.0)
        pmi_m = estimate_pmi_monthly(loan, price, None) if price > 0 else 0.0
        # Re-scale PMI with custom annual rate if needed
        if price > 0 and loan / price > LTV_PMI_THRESHOLD:
            pmi_m = loan * pmi_annual / 12.0
        housing = pi + tax_m + ins_m + hoa + pmi_m
        return {
            "price": round(price, 2),
            "down": round(down, 2),
            "loan": round(loan, 2),
            "principalAndInterest": round(pi, 2),
            "taxMonthly": round(tax_m, 2),
            "insuranceMonthly": round(ins_m, 2),
            "hoaMonthly": round(hoa, 2),
            "pmiMonthly": round(pmi_m, 2),
            "housingPayment": round(housing, 2),
        }

    max_detail = price_at_dti(DTI_RULE)
    comfort_detail = price_at_dti(DTI_COMFORT)

    return {
        "mode": "how_much_house",
        "dtiRule": DTI_RULE,
        "dtiComfort": DTI_COMFORT,
        "downPct": round(down_pct * 100, 2),
        "income": round(income, 2),
        "otherMonthlyDebts": round(debts, 2),
        "maxPrice": max_detail["price"],
        "comfortPrice": comfort_detail["price"],
        "max": max_detail,
        "comfort": comfort_detail,
        "pmiHeuristic": f"{pmi_annual * 100:.2f}% of loan / year when LTV > 80%",
    }


class LookupBody(BaseModel):
    address: Optional[str] = None
    id: Optional[str] = None
    sessionId: Optional[str] = None
    device: Optional[str] = None


class EventBody(BaseModel):
    type: str
    sessionId: Optional[str] = None
    address: Optional[str] = None
    device: Optional[str] = None
    payload: Any = None


class CalcBody(BaseModel):
    sessionId: Optional[str] = None
    device: Optional[str] = None
    address: Optional[str] = None
    mode: str = "this_home"  # this_home | how_much_house
    price: float = 0
    down: float = 0
    downPct: Optional[float] = None
    rate: float = 6.75
    term: int = 30
    taxAnnual: float = 0
    insuranceAnnual: float = 0
    taxRatePct: Optional[float] = None
    insRatePct: Optional[float] = None
    hoaMonthly: float = 0
    otherMonthlyDebts: float = 0
    income: float = 0
    pmiMonthly: Optional[float] = None  # None = auto
    extraPrincipal: float = 0
    housingCap: float = 31
    dtiCap: float = 43
    log: bool = False


@app.on_event("startup")
def startup() -> None:
    _init_db()


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "provider": type(provider).__name__,
        "apiKeyConfigured": bool(os.environ.get("PROPERTY_DATA_API_KEY", "").strip()),
        "dtiRule": DTI_RULE,
        "dtiComfort": DTI_COMFORT,
        "pmiAnnualRate": PMI_ANNUAL_RATE,
        "version": "0.3.0",
    }


@app.get("/api/suggest")
def suggest(
    request: Request,
    q: str = Query("", min_length=0),
    sessionId: Optional[str] = None,
    device: Optional[str] = None,
    logPartial: bool = Query(True),
):
    client = request.client.host if request.client else "unknown"
    if not _rate_ok(client):
        return JSONResponse({"error": "rate_limited", "results": []}, status_code=429)

    results = [s.to_dict() for s in provider.suggest(q, limit=8)]

    if logPartial and len(q.strip()) >= 2:
        _log_event(
            type="suggest",
            session_id=sessionId,
            address=q.strip(),
            device=device,
            user_agent=_ua(request),
            payload={"q": q, "resultCount": len(results), "ids": [r["id"] for r in results]},
        )

    return results


@app.post("/api/lookup")
def lookup(body: LookupBody, request: Request):
    detail = provider.lookup(address=body.address, id=body.id)
    if not detail:
        _log_event(
            type="lookup_miss",
            session_id=body.sessionId,
            address=body.address or body.id,
            device=body.device,
            user_agent=_ua(request),
            payload={"address": body.address, "id": body.id},
        )
        return JSONResponse({"error": "not_found"}, status_code=404)

    payload = detail.to_dict()
    # Attach min-income preview assuming 10% down (triggers PMI), default rate/term
    preview = compute_calc(
        {
            "price": detail.price,
            "down": detail.price * 0.10,
            "rate": 6.75,
            "term": 30,
            "taxAnnual": detail.taxAnnual,
            "insuranceAnnual": detail.insuranceAnnual,
            "hoaMonthly": detail.hoaMonthly,
            "otherMonthlyDebts": 0,
            "income": 0,
        }
    )
    payload["minMonthlyIncome"] = preview["minMonthlyIncome"]
    payload["minAnnualIncome"] = preview["minAnnualIncome"]
    payload["comfortMonthlyIncome"] = preview["comfortMonthlyIncome"]
    payload["comfortAnnualIncome"] = preview["comfortAnnualIncome"]
    payload["housingPaymentPreview"] = preview["housingPayment"]
    payload["pmiMonthlyPreview"] = preview["pmiMonthly"]

    _log_event(
        type="lookup",
        session_id=body.sessionId,
        address=detail.address,
        device=body.device,
        user_agent=_ua(request),
        payload=payload,
    )
    return payload


@app.post("/api/calc")
def calc_endpoint(body: CalcBody, request: Request):
    data = body.model_dump()
    if (body.mode or "this_home") == "how_much_house":
        result = compute_max_price(data)
    else:
        result = compute_calc(data)
    if body.log:
        _log_event(
            type="calc",
            session_id=body.sessionId,
            address=body.address,
            device=body.device,
            user_agent=_ua(request),
            payload={"inputs": data, "result": result},
        )
    return result


@app.post("/api/max-price")
def max_price_endpoint(body: CalcBody, request: Request):
    """Explicit how-much-house endpoint (also available via /api/calc mode=how_much_house)."""
    data = body.model_dump()
    result = compute_max_price(data)
    if body.log:
        _log_event(
            type="max_price",
            session_id=body.sessionId,
            address=body.address,
            device=body.device,
            user_agent=_ua(request),
            payload={"inputs": data, "result": result},
        )
    return result


@app.post("/api/events")
def post_event(body: EventBody, request: Request):
    eid = _log_event(
        type=body.type,
        session_id=body.sessionId,
        address=body.address,
        device=body.device,
        user_agent=_ua(request),
        payload=body.payload,
    )
    return {"ok": True, "id": eid}


@app.get("/api/events")
def get_events(limit: int = Query(50, ge=1, le=500)):
    with _conn() as c:
        rows = c.execute(
            """
            SELECT id, ts, type, session_id, address, device, user_agent, payload
            FROM events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        payload = None
        if r["payload"]:
            try:
                payload = json.loads(r["payload"])
            except json.JSONDecodeError:
                payload = r["payload"]
        out.append(
            {
                "id": r["id"],
                "ts": r["ts"],
                "type": r["type"],
                "sessionId": r["session_id"],
                "address": r["address"],
                "device": r["device"],
                "userAgent": r["user_agent"],
                "payload": payload,
            }
        )
    return out


@app.get("/api/stats")
def stats():
    with _conn() as c:
        total_lookups = c.execute(
            "SELECT COUNT(*) FROM events WHERE type = 'lookup'"
        ).fetchone()[0]
        sessions = c.execute(
            """
            SELECT session_id,
                   MAX(CASE WHEN type = 'session_start' THEN device END) AS start_device,
                   MAX(device) AS any_device
            FROM events
            WHERE session_id IS NOT NULL AND session_id != ''
            GROUP BY session_id
            """
        ).fetchall()

        device_rows = c.execute(
            """
            SELECT COALESCE(NULLIF(device, ''), 'unknown') AS d, COUNT(*) AS n
            FROM events
            GROUP BY d
            """
        ).fetchall()

    by_device_counts = {"phone": 0, "tablet": 0, "desktop": 0, "unknown": 0}
    for row in sessions:
        d = (row["start_device"] or row["any_device"] or "unknown").lower()
        if d not in by_device_counts:
            d = "unknown"
        by_device_counts[d] += 1

    total_sessions = sum(by_device_counts.values())
    event_mix = {r["d"]: r["n"] for r in device_rows}
    total_events = sum(event_mix.values())

    def pct(n: int, den: int) -> float:
        return round((n / den) * 100, 1) if den else 0.0

    by_device = {
        k: {"count": by_device_counts[k], "pct": pct(by_device_counts[k], total_sessions)}
        for k in ("phone", "tablet", "desktop", "unknown")
    }

    return {
        "totalSessions": total_sessions,
        "byDevice": by_device,
        "totalLookups": total_lookups,
        "totalEvents": total_events,
        "eventsByDevice": {
            k: {"count": event_mix.get(k, 0), "pct": pct(event_mix.get(k, 0), total_events)}
            for k in ("phone", "tablet", "desktop", "unknown")
        },
        "dtiRule": DTI_RULE,
        "dtiComfort": DTI_COMFORT,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8765, reload=False)


# ---- v1 launch layer ----
import v1 as _v1
_v1.init()
app.include_router(_v1.router)

@app.middleware("http")
async def _guard_admin_apis(request, call_next):
    # Raw event log / stats contain PII; admin only.
    if request.url.path in ("/api/events", "/api/stats") and request.method == "GET" and not _v1.authed(request):
        return JSONResponse({"error": "admin only"}, status_code=401)
    return await call_next(request)
