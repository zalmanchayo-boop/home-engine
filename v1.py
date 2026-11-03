"""v1 launch layer: levels, BUY + HELOC, rates, uploads, admin."""
from __future__ import annotations
import hashlib, hmac, html, json, os, secrets, sqlite3, time
from pathlib import Path
from typing import Optional
import httpx
from fastapi.responses import JSONResponse as _JR
from fastapi import APIRouter, File, Form, Request, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data"))).resolve()
DB = DATA_DIR / "events.db"  # same DB as server.py
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(DATA_DIR / "uploads"))).resolve()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
BOOKING_URL = os.environ.get("BOOKING_URL", "").strip() or "https://calendar.app.google/u9foEt42dhmBc4er9"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0").lower() in ("1", "true", "yes")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 15 * 1024 * 1024
ALLOWED = {".pdf": b"%PDF", ".jpg": b"\xff\xd8", ".jpeg": b"\xff\xd8", ".png": b"\x89PNG", ".heic": None}
DTI_RULE, DTI_COMFORT = 0.45, 0.36
router = APIRouter()

def con():
    c = sqlite3.connect(str(DB)); c.row_factory = sqlite3.Row; return c

def init():
    with con() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT, updated REAL);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, created REAL, last_seen REAL, device TEXT, ua TEXT,
          tab TEXT, level INTEGER DEFAULT 0, inputs TEXT DEFAULT '{}', results TEXT DEFAULT '{}', contact TEXT,
          consent INTEGER DEFAULT 0, call_requested INTEGER DEFAULT 0, intent TEXT);
        CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY, session_id TEXT, kind TEXT, orig_name TEXT,
          stored TEXT, ext TEXT, size INTEGER, ts REAL);
        CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, session_id TEXT, ip TEXT,
          limit_hit TEXT, name TEXT, email TEXT, phone TEXT);
        """)
        cols = [r[1] for r in c.execute("PRAGMA table_info(sessions)")]
        if "unlocked" not in cols: c.execute("ALTER TABLE sessions ADD COLUMN unlocked INTEGER DEFAULT 0")
        defaults = {"zalman_rate": "6.25", "national_rate": "6.30", "national_source": "Mortgage News Daily (placeholder - set by admin)",
                    "heloc_rate": "7.99", "rate_date": time.strftime("%Y-%m-%d")}
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings VALUES(?,?,?)", (k, v, time.time()))

def get_settings():
    with con() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}

def set_setting(k, v):
    with con() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES(?,?,?)", (k, str(v), time.time()))

# ---------- estimates ----------
# Approx. effective property tax rates (% of value/yr), Tax Foundation / Census ACS style figures.
STATE_TAX = {"AL":.39,"AK":1.0,"AZ":.52,"AR":.57,"CA":.71,"CO":.49,"CT":1.7,"DE":.55,"DC":.57,"FL":.82,"GA":.81,"HI":.27,
 "ID":.6,"IL":1.95,"IN":.75,"IA":1.43,"KS":1.29,"KY":.77,"LA":.51,"ME":1.0,"MD":.97,"MA":1.04,"MI":1.24,"MN":1.0,"MS":.67,
 "MO":.88,"MT":.67,"NE":1.48,"NV":.48,"NH":1.61,"NJ":2.08,"NM":.62,"NY":1.4,"NC":.7,"ND":.9,"OH":1.36,"OK":.8,"OR":.83,
 "PA":1.36,"RI":1.24,"SC":.5,"SD":1.08,"TN":.56,"TX":1.58,"UT":.5,"VT":1.6,"VA":.8,"WA":.84,"WV":.55,"WI":1.51,"WY":.55}
# Homeowners insurance heuristic (% of value/yr); hurricane / hail states higher.
STATE_INS = {"FL":1.1,"LA":1.0,"OK":1.0,"TX":0.85,"KS":0.8,"NE":0.8,"MS":0.75,"AL":0.7,"AR":0.7,"CO":0.6,"SC":0.55}
NYC_ZIP3 = {"100","101","102","103","104","110","111","112","113","114","116"}

def estimate_costs(price: float, state: str = "", zip_: str = ""):
    state = (state or "").upper()[:2]; z3 = (zip_ or "")[:3]
    tax_pct = STATE_TAX.get(state, 1.0); tax_note = f"{state or 'US'} avg effective rate {tax_pct:.2f}%"
    if state == "NY" and z3 in NYC_ZIP3:
        tax_pct, tax_note = 0.88, "NYC 1-3 family avg effective rate ~0.88%"
    ins_pct = STATE_INS.get(state, 0.40)
    return {"taxAnnual": round(price * tax_pct / 100), "insuranceAnnual": round(max(900, price * ins_pct / 100)),
            "taxPct": tax_pct, "insPct": ins_pct, "taxNote": tax_note,
            "insNote": f"{state or 'US'} heuristic {ins_pct:.2f}% of value", "label": "Estimate - edit anytime"}

from pmi import pmi_rate, BUCKETS

CREDIT_ADJ = {"760+": 0.0, "740-759": 0.125, "720-739": 0.25, "700-719": 0.375, "680-699": 0.625,
              "660-679": 0.875, "640-659": 1.25, "620-639": 1.75, "<620": 2.75}

def pmt(p, r, years=30):
    if p <= 0: return 0.0
    i = r / 1200; n = years * 12
    return p / n if i == 0 else p * i * (1 + i) ** n / ((1 + i) ** n - 1)

def buy_calc(d: dict):
    price = float(d.get("price") or 0); down = min(float(d.get("down") or 0), price)
    rate = float(d.get("rate") or 0); term = int(d.get("term") or 30)
    loan = price - down; ltv = loan / price if price else 0
    pi = pmt(loan, rate, term); tax = float(d.get("taxAnnual") or 0) / 12; ins = float(d.get("insuranceAnnual") or 0) / 12
    hoa = float(d.get("hoaMonthly") or 0)
    pb = d.get("pmiCredit"); pmi_r = pmi_rate(ltv, pb); pmi = loan * pmi_r / 1200
    housing = pi + tax + ins + hoa + pmi
    debts = float(d.get("debts") or 0); income = float(d.get("income") or 0)
    out = {"loan": loan, "ltv": ltv, "pi": pi, "tax": tax, "ins": ins, "hoa": hoa, "pmi": pmi, "pmiRatePct": pmi_r, "pmiEstimated": pb not in BUCKETS, "housing": housing,
           "minIncome": (housing + debts) / DTI_RULE, "comfortIncome": (housing + debts) / DTI_COMFORT}
    if income > 0:
        out["dti"] = (housing + debts) / income
        # buying power at 45% and 36%: same down %, same tax/ins % of price
        dp = down / price if price else 0.1
        tpct = (tax * 12 / price) if price else 0.01; ipct = (ins * 12 / price) if price else 0.004
        f = (1 - dp) * pmt(1, rate, term) + tpct / 12 + ipct / 12 + ((1 - dp) * pmi_rate(1 - dp, pb) / 1200)
        out["maxPrice"] = max(income * DTI_RULE - debts - hoa, 0) / f if f else 0
        out["comfortPrice"] = max(income * DTI_COMFORT - debts - hoa, 0) / f if f else 0
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}

def heloc_calc(d: dict, heloc_rate: float):
    v = float(d.get("value") or 0); bal = float(d.get("balance") or 0); first = float(d.get("firstPayment") or 0)
    tiers = []
    for c in (0.80, 0.85, 0.90):
        avail = max(v * c - bal, 0)
        tiers.append({"cltv": c, "available": round(avail), "ioPayment": round(avail * heloc_rate / 1200, 2)})
    out = {"equity": round(max(v - bal, 0)), "ltv": round(bal / v, 4) if v else 0, "tiers": tiers, "helocRate": heloc_rate}
    income = float(d.get("income") or 0); debts = float(d.get("debts") or 0)
    if income > 0:
        draw = float(d.get("draw") or tiers[1]["available"])
        io = draw * heloc_rate / 1200
        dti = (first + io + debts) / income
        room = max(income * DTI_RULE - first - debts, 0)
        out.update({"draw": draw, "drawPayment": round(io, 2), "dti": round(dti, 4), "maxDrawByDti": round(room * 1200 / heloc_rate),
                    "pass": dti <= DTI_RULE})
    return out

# ---------- sessions ----------
def touch(sid: str, req: Request, *, tab=None, level=None, inputs=None, results=None, device=None):
    if not sid or len(sid) > 64: raise HTTPException(400, "bad session")
    now = time.time()
    with con() as c:
        r = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not r:
            c.execute("INSERT INTO sessions(id,created,last_seen,device,ua,tab,level) VALUES(?,?,?,?,?,?,0)",
                      (sid, now, now, device or "unknown", req.headers.get("user-agent", "")[:300], tab or "buy"))
            r = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        inp = json.loads(r["inputs"] or "{}"); res = json.loads(r["results"] or "{}")
        if inputs and tab: inp.setdefault(tab, {}).update(inputs)
        if results and tab: res[tab] = results
        c.execute("UPDATE sessions SET last_seen=?, tab=COALESCE(?,tab), level=MAX(level,?), inputs=?, results=?, device=COALESCE(?,device) WHERE id=?",
                  (now, tab, level or 0, json.dumps(inp), json.dumps(res), device, sid))

@router.get("/api/v1/config")
def config():
    if time.time() - _last_try[0] >= REFRESH_SECS: refresh_national()
    s = get_settings()
    return {"zalmanRate": float(s["zalman_rate"]), "nationalRate": float(s["national_rate"]), "nationalSource": s["national_source"],
            "rateDate": s["rate_date"], "nationalAsOf": s.get("national_asof", ""), "nationalMode": s.get("national_mode", "auto"), "helocRate": float(s["heloc_rate"]), "creditBuckets": list(CREDIT_ADJ), "bookingUrl": BOOKING_URL, "googleMapsKey": os.environ.get("GOOGLE_MAPS_API_KEY", "").strip(),
            "creditAdj": CREDIT_ADJ, "dtiRule": DTI_RULE, "dtiComfort": DTI_COMFORT}

@router.get("/api/v1/geo")
def geo(q: str):
    """Keyless US Census geocoder: validates address, returns state/ZIP for estimates."""
    if len(q.strip()) < 6: return {"results": []}
    try:
        r = httpx.get("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                      params={"address": q, "benchmark": "Public_AR_Current", "format": "json"}, timeout=6)
        m = r.json()["result"]["addressMatches"][:5]
        return {"results": [{"label": x["matchedAddress"], "state": x["addressComponents"]["state"],
                             "zip": x["addressComponents"]["zip"]} for x in m], "source": "US Census Geocoder"}
    except Exception:
        return {"results": [], "error": "geocoder_unavailable"}

@router.post("/api/v1/estimate")
async def estimate(req: Request):
    d = await req.json(); return estimate_costs(float(d.get("price") or 0), d.get("state", ""), d.get("zip", ""))

@router.post("/api/v1/step")
async def step(req: Request):
    """Called by UI on each level submit. body: sessionId, device, tab(buy|heloc), level(1-2), inputs."""
    d = await req.json(); tab = d.get("tab") if d.get("tab") in ("buy", "heloc") else "buy"
    lvl = max(1, min(int(d.get("level") or 1), 2)); inp = d.get("inputs") or {}
    s = get_settings(); bucket = inp.get("credit")
    adj = CREDIT_ADJ.get(bucket, 0.0) if lvl >= 2 else 0.0
    if tab == "buy":
        if lvl >= 2 and bucket: inp = {**inp, "rate": float(inp.get("baseRate") or s["zalman_rate"]) + adj}
        res = buy_calc({**inp, "pmiCredit": bucket if lvl >= 2 else None}); res["rateUsed"] = inp.get("rate"); res["creditAdj"] = adj
    else:
        res = heloc_calc(inp, float(s["heloc_rate"]) + adj); res["creditAdj"] = adj
    touch(d.get("sessionId"), req, tab=tab, level=lvl, inputs=inp, results=res, device=d.get("device"))
    return res

@router.post("/api/v1/ping")
async def ping(req: Request):
    d = await req.json(); touch(d.get("sessionId"), req, tab=d.get("tab"), device=d.get("device")); return {"ok": True}

@router.post("/api/v1/verify")
async def verify(req: Request, sessionId: str = Form(...), tab: str = Form("buy"), name: str = Form(...), email: str = Form(...),
                 phone: str = Form(...), consent: str = Form(...), intent: str = Form("verified"), employment: str = Form(""),
                 cashflow: str = Form(""), device: str = Form(""), files: list[UploadFile] = File(default=[]), kinds: str = Form(""), website: str = Form("")):
    import ratelimit as rl
    if website.strip():  # honeypot: silently drop bots
        return {"ok": True, "uploaded": len([f for f in files if f.filename]), "intent": intent}
    ip = rl.client_ip(req); sid = sessionId[:64]; nf = len([f for f in files[:12] if f.filename])
    if nf and not rl.disk_ok(UPLOAD_DIR): raise HTTPException(507, "Uploads are paused for a moment. Please call 917-499-9599 or try again later.")
    if not rl.hit("verify", ip, 5, 3600, peek=True, sid=sid) or not rl.hit("files", ip, 30, 86400, n=nf, peek=True, sid=sid) or (intent == "call" and not rl.hit("call", ip, 3, 3600, peek=True, sid=sid)):
        which = "call" if intent == "call" and not rl.hit("call", ip, 3, 3600, peek=True, sid=sid) else ("files" if not rl.hit("files", ip, 30, 86400, n=nf, peek=True, sid=sid) else "verify")
        return rl.too_many(which, ip, sid)
    rl.hit("verify", ip, 5, 3600, sid=sid); rl.hit("files", ip, 30, 86400, n=nf, sid=sid)
    if intent == "call": rl.hit("call", ip, 3, 3600, sid=sid)
    if consent not in ("1", "true", "on"): raise HTTPException(400, "consent required")
    if "@" not in email or len(phone.strip()) < 7 or not name.strip(): raise HTTPException(400, "name, email and phone required")
    tab = tab if tab in ("buy", "heloc") else "buy"
    kind_list = kinds.split(",") if kinds else []
    saved = []
    for i, f in enumerate(files[:12]):
        if not f.filename: continue
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED: raise HTTPException(400, f"{f.filename}: only PDF, JPG, PNG, HEIC")
        data = await f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES: raise HTTPException(400, f"{f.filename}: over 15MB")
        magic = ALLOWED[ext]
        if (magic and not data.startswith(magic)) or (ext == ".heic" and b"ftyp" not in data[:16]):
            raise HTTPException(400, f"{f.filename}: file content does not match type")
        fid = secrets.token_hex(16); (UPLOAD_DIR / (fid + ext)).write_bytes(data)
        saved.append((fid, sessionId, kind_list[i] if i < len(kind_list) else "doc", f.filename[:200], fid + ext, ext, len(data), time.time()))
    touch(sessionId, req, tab=tab, level=3, inputs={"employment": employment, "cashflow": cashflow}, device=device or None)
    with con() as c:
        c.executemany("INSERT INTO uploads VALUES(?,?,?,?,?,?,?,?)", saved)
        c.execute("UPDATE sessions SET contact=?, consent=1, call_requested=MAX(call_requested,?), intent=? WHERE id=?",
                  (json.dumps({"name": name[:120], "email": email[:160], "phone": phone[:40]}), 1 if intent == "call" else 0, intent, sessionId))
    return {"ok": True, "uploaded": len(saved), "intent": intent}

@router.post("/api/v1/unlock")
async def unlock_endpoint(req: Request):
    import ratelimit as rl, re
    try: d = await req.json()
    except Exception: raise HTTPException(400, "bad request")
    ip = rl.client_ip(req); sid = str(d.get("sessionId") or rl.sid_of(req))[:64]
    if not rl.hit("unlock", ip, 3, 3600): return _JR(
        {"detail": "Too many unlock attempts. Please try again later or call 917-499-9599.", "error": "rate_limited", "unlock": False}, status_code=429)
    if str(d.get("website") or "").strip(): return {"ok": True}  # honeypot
    name = str(d.get("name") or "").strip()[:120]; email = str(d.get("email") or "").strip()[:160]
    phone = re.sub(r"\D", "", str(d.get("phone") or ""))[-15:]
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(phone) < 10:
        raise HTTPException(400, "Please enter your name, a valid email and a 10-digit phone.")
    if str(d.get("consent")) not in ("1", "true", "True", "on"): raise HTTPException(400, "Please check the consent box.")
    limit = str(d.get("limit") or "api")[:20]
    with con() as c:
        c.execute("INSERT INTO leads(ts,session_id,ip,limit_hit,name,email,phone) VALUES(?,?,?,?,?,?,?)",
                  (time.time(), sid, ip, limit, name, email, phone))
    if sid:
        touch(sid, req)
        with con() as c:
            c.execute("UPDATE sessions SET unlocked=1, consent=1, contact=COALESCE(contact, ?) WHERE id=?",
                      (json.dumps({"name": name, "email": email, "phone": phone}), sid))
    already = rl.is_unlocked(ip, sid)
    if not already: rl.unlock(ip, sid)
    return {"ok": True, "doubled": not already}

# ---------- national rate (auto) ----------
# Default: Freddie Mac PMMS 30-yr fixed, weekly, via FRED (public, no key). Optional override feed via
# NATIONAL_RATE_FEED_URL (JSON {"rate":6.31,"source":"...","asOf":"YYYY-MM-DD"}), e.g. a licensed MND feed.
# Admin manual entry sets national_mode=manual; "Pull national avg" switches back to auto.
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=MORTGAGE30US"
PMMS_URL = "https://www.freddiemac.com/pmms/docs/PMMS_history.csv"
REFRESH_SECS = 6 * 3600
_last_try = [0.0]

def _fmt_week(d):
    try: return time.strftime("%b %-d, %Y", time.strptime(d, "%Y-%m-%d"))
    except Exception: return d

def _from_fred():
    r = httpx.get(FRED_URL, timeout=8, follow_redirects=True); r.raise_for_status()
    for line in reversed(r.text.strip().splitlines()[1:]):
        d, v = line.split(",")[:2]
        if v not in (".", ""): return float(v), d
    raise ValueError("no data")

def _from_pmms():
    r = httpx.get(PMMS_URL, timeout=8, follow_redirects=True); r.raise_for_status()
    rows = r.text.strip().splitlines(); hdr = [h.strip().lower() for h in rows[0].split(",")]
    i = hdr.index("pmms30")
    for line in reversed(rows[1:]):
        f = line.split(",")
        if len(f) > i and f[i].strip():
            m, d, y = f[0].split("/"); return float(f[i]), f"{y}-{int(m):02d}-{int(d):02d}"
    raise ValueError("no data")

def fetch_national():
    """Returns (rate, source_label, as_of) or None."""
    url = os.environ.get("NATIONAL_RATE_FEED_URL", "").strip()
    if url:
        try:
            j = httpx.get(url, timeout=8).json(); asof = j.get("asOf", time.strftime("%Y-%m-%d"))
            return float(j["rate"]), j.get("source", "Custom feed"), asof
        except Exception: pass
    for fn in (_from_fred, _from_pmms):
        try:
            rate, d = fn(); return rate, f"National avg 30-yr (Freddie Mac PMMS, week of {_fmt_week(d)})", d
        except Exception: continue
    return None

def refresh_national(force=False):
    s = get_settings()
    if not force and s.get("national_mode", "auto") == "manual": return
    if not force and time.time() - _last_try[0] < REFRESH_SECS: return
    _last_try[0] = time.time()
    got = fetch_national()
    if got:
        set_setting("national_rate", got[0]); set_setting("national_source", got[1])
        set_setting("national_asof", got[2]); set_setting("national_mode", "auto")

def _bg_refresh():
    import threading; threading.Thread(target=refresh_national, daemon=True).start()

# ---------- admin ----------
def _pw(): return os.environ.get("ADMIN_PASSWORD", "")
def _tok(): return hmac.new(_pw().encode(), b"hae-admin", hashlib.sha256).hexdigest()
def authed(req: Request): return bool(_pw()) and hmac.compare_digest(req.cookies.get("hae_admin", ""), _tok())

LOGIN = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>Admin</title>
<body style="font-family:system-ui;display:grid;place-items:center;height:100vh;background:#0b0f14;color:#fff">
<form method=post action=/admin/login style="display:grid;gap:12px;width:280px"><h2>Engine admin</h2>{err}
<input type=password name=password placeholder=Password style="padding:14px;border-radius:12px;border:0">
<button style="padding:14px;border-radius:12px;border:0;background:#00c805;font-weight:700">Sign in</button></form>"""

@router.get("/admin", response_class=HTMLResponse)
def admin(req: Request):
    if not authed(req): return HTMLResponse(LOGIN.format(err=""))
    return HTMLResponse(render_admin())

@router.post("/admin/login")
def admin_login(password: str = Form(...)):
    if not _pw() or not hmac.compare_digest(password, _pw()):
        time.sleep(1); return HTMLResponse(LOGIN.format(err="<p style=color:#f66>Wrong password</p>"), status_code=401)
    r = RedirectResponse("/admin", 303); r.set_cookie("hae_admin", _tok(), httponly=True, samesite="strict", max_age=86400 * 7, secure=COOKIE_SECURE); return r

@router.get("/admin/logout")
def admin_logout():
    r = RedirectResponse("/admin", 303); r.delete_cookie("hae_admin"); return r

@router.post("/admin/rates")
def admin_rates(req: Request, zalman_rate: str = Form(...), national_rate: str = Form(""), national_source: str = Form(""), heloc_rate: str = Form("")):
    if not authed(req): raise HTTPException(401)
    set_setting("zalman_rate", float(zalman_rate)); set_setting("rate_date", time.strftime("%Y-%m-%d"))
    if national_rate and abs(float(national_rate) - float(get_settings()["national_rate"])) > 1e-9:
        set_setting("national_rate", float(national_rate)); set_setting("national_mode", "manual")
        set_setting("national_source", (national_source or "Set by admin")[:200]); set_setting("national_asof", time.strftime("%Y-%m-%d"))
    elif national_source and national_source != get_settings()["national_source"]: set_setting("national_source", national_source[:200])
    if heloc_rate: set_setting("heloc_rate", float(heloc_rate))
    return RedirectResponse("/admin", 303)

@router.post("/admin/refresh-national")
def admin_refresh(req: Request):
    if not authed(req): raise HTTPException(401)
    refresh_national(force=True)
    return RedirectResponse("/admin", 303)

@router.get("/admin/file/{fid}")
def admin_file(fid: str, req: Request):
    if not authed(req): raise HTTPException(401)
    with con() as c: r = c.execute("SELECT * FROM uploads WHERE id=?", (fid,)).fetchone()
    if not r: raise HTTPException(404)
    return FileResponse(UPLOAD_DIR / r["stored"], filename=r["orig_name"])

@router.get("/admin/api/stats")
def admin_stats(req: Request):
    if not authed(req): raise HTTPException(401)
    return stats()

def stats():
    with con() as c:
        rows = c.execute("SELECT device, level, call_requested, tab FROM sessions").fetchall()
    n = len(rows) or 1
    dev = {}
    for r in rows: dev[r["device"]] = dev.get(r["device"], 0) + 1
    f = {"visits": len(rows), "L1": sum(r["level"] >= 1 for r in rows), "L2": sum(r["level"] >= 2 for r in rows),
         "L3": sum(r["level"] >= 3 for r in rows), "call": sum(r["call_requested"] for r in rows)}
    return {"funnel": f, "devices": {k: {"n": v, "pct": round(100 * v / n, 1)} for k, v in dev.items()},
            "tabs": {t: sum(r["tab"] == t for r in rows) for t in ("buy", "heloc")}}

def render_admin():
    s = get_settings(); st = stats(); e = html.escape
    with con() as c:
        sess = c.execute("SELECT * FROM sessions ORDER BY call_requested DESC, (contact IS NOT NULL) DESC, last_seen DESC LIMIT 500").fetchall()
        ups = {}
        for u in c.execute("SELECT * FROM uploads ORDER BY ts"): ups.setdefault(u["session_id"], []).append(u)
        leads = c.execute("SELECT * FROM leads ORDER BY ts DESC LIMIT 500").fetchall()
    lrows = "".join(f"<tr><td>{time.strftime('%m/%d %I:%M%p', time.localtime(l['ts']))}</td><td>{e(l['name'])}</td><td>{e(l['email'])}</td>"
                    f"<td>{e(l['phone'])}</td><td>{e(l['limit_hit'])}</td><td>{e(l['ip'])}</td><td>{e((l['session_id'] or '')[:8])}</td></tr>" for l in leads)
    f = st["funnel"]; base = f["visits"] or 1
    funnel = "".join(f"<div class=card><b>{f[k]}</b><span>{lab} · {round(100*f[k]/base)}%</span></div>"
                     for k, lab in (("visits", "Sessions"), ("L1", "Level 1"), ("L2", "Level 2"), ("L3", "Level 3 verify"), ("call", "Call requests")))
    devs = "".join(f"<div class=card><b>{v['pct']}%</b><span>{e(k)} ({v['n']})</span></div>" for k, v in st["devices"].items())
    trs = []
    for r in sess:
        ct = json.loads(r["contact"]) if r["contact"] else None
        flag = "<span class=flag>CALL</span>" if r["call_requested"] else ("<span class=flag2>VERIFIED</span>" if ct and r["level"] >= 3 else "")
        if r["unlocked"]: flag += "<span class=flag3>Unlocked via limit</span>"
        files = "".join(f"<a href='/admin/file/{u['id']}'>{e(u['kind'])}: {e(u['orig_name'])}</a><br>" for u in ups.get(r["id"], []))
        trs.append(f"<tr class={'hot' if r['call_requested'] else ''}><td>{flag}{time.strftime('%m/%d %I:%M%p', time.localtime(r['last_seen']))}</td>"
                   f"<td>{e(r['device'] or '')}</td><td>{e(r['tab'] or '')}</td><td>L{r['level']}</td>"
                   f"<td>{e(ct['name'])+'<br>'+e(ct['email'])+'<br>'+e(ct['phone']) if ct else '-'}</td>"
                   f"<td><details><summary>inputs</summary><pre>{e(json.dumps(json.loads(r['inputs']), indent=1))}</pre>"
                   f"<pre>{e(json.dumps(json.loads(r['results']), indent=1))}</pre></details></td><td>{files or '-'}</td></tr>")
    return f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>Engine admin</title>
<style>body{{font-family:Inter,system-ui;background:#0b0f14;color:#e8eef3;margin:0;padding:20px}}a{{color:#5fb3e0}}
.row{{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 24px}}.card{{background:#141b22;border-radius:14px;padding:14px 18px;min-width:120px}}
.card b{{display:block;font-size:28px}}.card span{{color:#8a9aa8;font-size:13px}}table{{width:100%;border-collapse:collapse;font-size:13px}}
td,th{{border-bottom:1px solid #222c36;padding:8px;vertical-align:top;text-align:left}}tr.hot{{background:#10261a}}
.flag{{background:#00c805;color:#000;border-radius:6px;padding:2px 6px;font-weight:700;margin-right:6px}}.flag3{{background:#d4b483;color:#000;border-radius:6px;padding:2px 6px;margin-right:6px;font-weight:700}}.flag2{{background:#327AA3;border-radius:6px;padding:2px 6px;margin-right:6px}}
pre{{white-space:pre-wrap;max-width:420px;font-size:11px}}input{{padding:10px;border-radius:10px;border:0;margin:4px}}button{{padding:10px 16px;border-radius:10px;border:0;background:#00c805;font-weight:700}}</style>
<h1>Home Engine admin <a style=font-size:13px href=/admin/logout>log out</a></h1>
<form method=post action=/admin/rates class=card>Today's rate ({e(s['rate_date'])}):
<input name=zalman_rate value="{e(s['zalman_rate'])}" size=6> National avg <input name=national_rate value="{e(s['national_rate'])}" size=6>
Source <input name=national_source value="{e(s['national_source'])}" size=30> HELOC rate <input name=heloc_rate value="{e(s['heloc_rate'])}" size=6>
<button>Save</button></form>
<form method=post action=/admin/refresh-national style=margin-top:8px><button style=background:#327AA3;color:#fff>Pull national avg from feed</button>
<small> (Freddie Mac PMMS via FRED; switches back to auto)</small></form>
<h3>Funnel</h3><div class=row>{funnel}</div><h3>Devices</h3><div class=row>{devs}</div>
<h3>Leads (unlocked via limit)</h3><table><tr><th>When</th><th>Name</th><th>Email</th><th>Phone</th><th>Limit hit</th><th>IP</th><th>Session</th></tr>{lrows or "<tr><td colspan=7>None yet</td></tr>"}</table>
<h3>Sessions (call requests first)</h3><table><tr><th>Last seen</th><th>Device</th><th>Tab</th><th>Level</th><th>Contact</th><th>Data</th><th>Uploads</th></tr>{''.join(trs)}</table>"""
