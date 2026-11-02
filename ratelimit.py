"""In-memory per-IP abuse limits (single instance on Render)."""
import shutil, time, threading
from collections import defaultdict, deque
from fastapi import Request
from fastapi.responses import JSONResponse

_lock = threading.Lock()
_hits: dict = defaultdict(deque)
MSG = "Whoa, that's a lot of requests. Please wait a bit and try again, or call 917-499-9599."

def client_ip(req: Request) -> str:
    xff = req.headers.get("x-forwarded-for", "")
    if xff.strip():
        return xff.split(",")[0].strip()
    return req.client.host if req.client else "?"

UNLOCK_SECS = 86400
_unlocked: dict = {}  # key ("ip:..." / "sid:...") -> ts; mirrored in SQLite table unlocks
_db = [None]

def _conn():
    import sqlite3
    c = sqlite3.connect(str(_db[0])); c.row_factory = sqlite3.Row; return c

def load(db_path):
    _db[0] = db_path
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS unlocks(key TEXT PRIMARY KEY, ts REAL)")
        for r in c.execute("SELECT key, ts FROM unlocks WHERE ts > ?", (time.time() - UNLOCK_SECS,)):
            _unlocked[r["key"]] = r["ts"]

def is_unlocked(ip: str, sid: str = "") -> bool:
    now = time.time()
    return any(k and now - _unlocked.get(k, 0) < UNLOCK_SECS for k in ("ip:" + ip, ("sid:" + sid) if sid else ""))

def unlock(ip: str, sid: str = ""):
    now = time.time(); keys = ["ip:" + ip] + (["sid:" + sid] if sid else [])
    for k in keys: _unlocked[k] = now
    if _db[0]:
        with _conn() as c:
            c.executemany("INSERT OR REPLACE INTO unlocks VALUES(?,?)", [(k, now) for k in keys])

def sid_of(req: Request) -> str:
    return (req.cookies.get("hae_sid") or req.query_params.get("sessionId") or "")[:64]

def hit(bucket: str, ip: str, limit: int, window: float, n: int = 1, peek: bool = False, sid: str = "") -> bool:
    """Record n hits; False if it would exceed limit (doubled once after a lead unlock)."""
    if bucket != "unlock" and is_unlocked(ip, sid): limit *= 2
    now = time.time(); key = (bucket, ip)
    with _lock:
        q = _hits[key]
        while q and now - q[0] >= window: q.popleft()
        if len(q) + n > limit: return False
        if not peek: q.extend([now] * n)
        if len(_hits) > 50000:  # crude cleanup
            for k in [k for k, v in _hits.items() if not v][:10000]: _hits.pop(k, None)
        return True

def too_many(limit: str = "api", ip: str = "", sid: str = ""):
    return JSONResponse({"detail": MSG, "error": "rate_limited", "limit": limit, "unlock": not is_unlocked(ip, sid)},
                        status_code=429, headers={"Retry-After": "600"})

ADDR = ("/api/suggest", "/api/lookup", "/api/v1/geo")

async def middleware(request: Request, call_next):
    p = request.url.path
    if p.startswith("/api/") and p not in ("/api/health", "/api/v1/unlock"):
        ip = client_ip(request); sid = sid_of(request)
        if p in ADDR and not hit("addr", ip, 60, 3600, sid=sid): return too_many("addr", ip, sid)
        if not hit("api", ip, 300, 3600, sid=sid): return too_many("api", ip, sid)
    return await call_next(request)

def disk_ok(path, min_free=100 * 1024 * 1024) -> bool:
    try: return shutil.disk_usage(str(path)).free >= min_free
    except Exception: return True
