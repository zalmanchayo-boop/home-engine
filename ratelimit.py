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

def hit(bucket: str, ip: str, limit: int, window: float, n: int = 1, peek: bool = False) -> bool:
    """Record n hits; False if it would exceed limit."""
    now = time.time(); key = (bucket, ip)
    with _lock:
        q = _hits[key]
        while q and now - q[0] >= window: q.popleft()
        if len(q) + n > limit: return False
        if not peek: q.extend([now] * n)
        if len(_hits) > 50000:  # crude cleanup
            for k in [k for k, v in _hits.items() if not v][:10000]: _hits.pop(k, None)
        return True

def too_many():
    return JSONResponse({"detail": MSG, "error": "rate_limited"}, status_code=429, headers={"Retry-After": "600"})

ADDR = ("/api/suggest", "/api/lookup", "/api/v1/geo")

async def middleware(request: Request, call_next):
    p = request.url.path
    if p.startswith("/api/") and p != "/api/health":
        ip = client_ip(request)
        if p in ADDR and not hit("addr", ip, 60, 3600): return too_many()
        if not hit("api", ip, 300, 3600): return too_many()
    return await call_next(request)

def disk_ok(path, min_free=100 * 1024 * 1024) -> bool:
    try: return shutil.disk_usage(str(path)).free >= min_free
    except Exception: return True
