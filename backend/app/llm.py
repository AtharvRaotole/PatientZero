"""Optional LLM phrasing (off by default). The LLM never picks Cypher and never sees the database.

It may only rephrase the deterministic answer in one sentence. The sentence is rejected if it names any service
or package that is not in the graph result.
"""
from __future__ import annotations

import re

import httpx

from . import config


def allowed_names(resp: dict) -> set[str]:
    names: set[str] = set()
    for group in ("exposed", "safe"):
        for e in resp.get(group) or []:
            names.add(e["service"])
            for f in e["findings"]:
                names.add(f["package"])
                names.update(n.split("@")[0] for n in f["path"]["via"])
    for s in resp.get("scope") or []:
        names.add(s["package"])
    return {n.lower() for n in names}


def validate(sentence: str, resp: dict, known: set[str]) -> bool:
    """Every token that looks like a fleet service or a known package must appear in the result."""
    ok = allowed_names(resp)
    for tok in re.findall(r"[@a-z0-9][a-z0-9._/-]*", sentence.lower()):
        tok = tok.split("@")[0] if not tok.startswith("@") else tok
        if tok in known and tok not in ok:
            return False
    return True


def summarize(resp: dict, known: set[str]) -> str | None:
    if not (config.LLM_ENABLED and config.LLM_API_KEY):
        return None
    prompt = ("Rephrase this incident triage result as ONE short sentence for an on-call engineer. "
              "Only use names that appear in it.\n\n" + resp["message"])
    try:
        r = httpx.post(f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions", timeout=8,
                       headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                       json={"model": config.LLM_MODEL, "temperature": 0,
                             "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        s = r.json()["choices"][0]["message"]["content"].strip()
    except Exception:  # noqa: BLE001 - optional feature, fall back to the templated sentence
        return None
    return s if validate(s, resp, known) else None
