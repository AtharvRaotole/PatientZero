"""Hard spend guard for the optional LLM: a server-side daily call cap, a per-IP rate limit, and a usage log.

Every OpenAI HTTP call goes through `acquire()` first. When a limit is hit, `BudgetExceeded` is raised and the
agent falls back to the deterministic router with a visible note in the trace.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from .. import config


class BudgetExceeded(RuntimeError):
    pass


_lock = threading.Lock()
_ip_hits: dict[str, deque] = defaultdict(deque)
_day = {"date": "", "calls": 0, "tokens": 0}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _state_file() -> Path | None:
    return Path(config.LLM_USAGE_DIR) / "llm_budget.json" if config.LLM_USAGE_DIR else None


def _load() -> None:
    if _day["date"] == _today():
        return
    _day.update(date=_today(), calls=0, tokens=0)
    f = _state_file()
    if f and f.exists():
        try:
            saved = json.loads(f.read_text())
            if saved.get("date") == _today():
                _day.update(saved)
        except (OSError, ValueError):
            pass


def _save() -> None:
    f = _state_file()
    if f:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(_day))
        except OSError:
            pass


def acquire(ip: str = "") -> None:
    """Reserve one LLM call or raise BudgetExceeded."""
    now = time.time()
    with _lock:
        _load()
        if _day["calls"] >= config.OPENAI_DAILY_CALL_CAP:
            raise BudgetExceeded(f"daily LLM cap reached ({config.OPENAI_DAILY_CALL_CAP} calls)")
        key = ip or "unknown"
        q = _ip_hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= config.OPENAI_PER_IP_PER_MIN:
            raise BudgetExceeded(f"per-client LLM limit reached ({config.OPENAI_PER_IP_PER_MIN}/min)")
        q.append(now)
        _day["calls"] += 1
        _save()


def record(kind: str, tokens: int, ip: str = "", model: str = "") -> None:
    """Append token usage to the usage log (JSON lines). The client IP is stored only as a short hash."""
    with _lock:
        _load()
        _day["tokens"] += int(tokens or 0)
        _save()
    if not config.LLM_USAGE_DIR:
        return
    line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "model": model,
            "tokens": int(tokens or 0), "client": hashlib.sha256((ip or "").encode()).hexdigest()[:10]}
    try:
        p = Path(config.LLM_USAGE_DIR) / "llm_usage.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(json.dumps(line) + "\n")
    except OSError:
        pass


def status() -> dict:
    with _lock:
        _load()
        return {"date": _day["date"], "calls": _day["calls"], "tokens": _day["tokens"],
                "daily_cap": config.OPENAI_DAILY_CALL_CAP, "per_ip_per_min": config.OPENAI_PER_IP_PER_MIN}


def reset_for_tests() -> None:
    with _lock:
        _ip_hits.clear()
        _day.update(date=_today(), calls=0, tokens=0)
