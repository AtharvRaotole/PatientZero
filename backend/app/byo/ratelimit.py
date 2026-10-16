"""In-memory sliding-window rate limiter (per client IP). Good enough for a single-instance demo deploy."""
from __future__ import annotations

import ipaddress
import os
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> float:
        """Record a hit. Returns 0 if allowed, else seconds until the next slot frees up."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return round(self.window - (now - q[0]), 1) if q else round(self.window, 1)
            q.append(now)
            if len(self._hits) > 10_000:  # bound memory
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return 0.0

    def reset(self):
        with self._lock:
            self._hits.clear()


# Which proxy header carries the client address. Railway's edge sets X-Real-IP to the client's remote IP and
# overwrites any copy the caller sends (docs.railway.com/networking/public-networking/specs-and-limits), so it is the
# documented, spoof-resistant source there. X-Forwarded-For has no fixed hop count on Railway and is not used unless
# TRUSTED_PROXY_HOPS says how many proxies we run behind (then the right-most untrusted hop is the client).
# Off Railway with neither set, only the socket peer is trusted: a header a client can write is never an identity.
_ON_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_PROJECT_ID")
                   or os.getenv("RAILWAY_ENVIRONMENT"))
CLIENT_IP_HEADER = (os.getenv("CLIENT_IP_HEADER") or ("x-real-ip" if _ON_RAILWAY else "")).strip().lower()
TRUSTED_PROXY_HOPS = max(0, int(os.getenv("TRUSTED_PROXY_HOPS", "0") or 0))


def _ip(value: str | None) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        return None


def client_ip(request, *, header: str | None = None, hops: int | None = None) -> str:
    """The client address used for rate limits.

    1. CLIENT_IP_HEADER (X-Real-IP on Railway, set by the edge), when it holds a valid IP.
    2. With TRUSTED_PROXY_HOPS=N, the N-th X-Forwarded-For entry from the right (the address our own N proxies saw).
    3. The socket peer."""
    header = CLIENT_IP_HEADER if header is None else header
    hops = TRUSTED_PROXY_HOPS if hops is None else hops
    if header:
        ip = _ip(request.headers.get(header))
        if ip:
            return ip
    if hops:
        parts = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
        if len(parts) >= hops:
            ip = _ip(parts[-hops])
            if ip:
                return ip
    return request.client.host if request.client else "unknown"
