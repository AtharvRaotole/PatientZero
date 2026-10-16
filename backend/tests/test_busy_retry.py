"""FalkorDB 'Max pending queries exceeded' (queue full, MAX_QUEUED_QUERIES) is retried, never surfaced as a 500."""
import pytest

from app import db


def test_busy_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(db.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Max pending queries exceeded")
        return "ok"
    assert db.with_busy_retry(flaky) == "ok" and calls["n"] == 3


def test_other_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(db.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise ValueError("syntax error")
    with pytest.raises(ValueError):
        db.with_busy_retry(bad)
    assert calls["n"] == 1


def test_gives_up_after_budget(monkeypatch):
    monkeypatch.setattr(db.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        db.with_busy_retry(lambda: (_ for _ in ()).throw(RuntimeError("Max pending queries exceeded")))
