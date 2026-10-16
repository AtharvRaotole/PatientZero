"""OSV access for the watcher, with timeouts and size caps on every outbound fetch.

Index: https://osv-vulnerabilities.storage.googleapis.com/npm/modified_id.csv (newest first, "<modified>,<id>").
The full file is about 9 MB, so only the head is fetched with an HTTP Range request (WATCH_INDEX_BYTES, default
128 KB, a few thousand newest entries) and the ETag is remembered so an unchanged index costs one 304.
Records: https://api.osv.dev/v1/vulns/<id>, capped at WATCH_RECORD_MAX_BYTES each.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
import time

import httpx

INDEX_URL = os.getenv("WATCH_INDEX_URL", "https://osv-vulnerabilities.storage.googleapis.com/npm/modified_id.csv")
RECORD_URL = os.getenv("WATCH_RECORD_URL", "https://api.osv.dev/v1/vulns/{id}")
INDEX_BYTES = int(os.getenv("WATCH_INDEX_BYTES", str(128 * 1024)))
RECORD_MAX_BYTES = int(os.getenv("WATCH_RECORD_MAX_BYTES", str(512 * 1024)))
TIMEOUT = float(os.getenv("WATCH_HTTP_TIMEOUT", "10"))
WORKERS = int(os.getenv("WATCH_FETCH_WORKERS", "8"))
ID_RE = re.compile(r"^MAL-\d{4}-\d{1,7}$")
UA = {"User-Agent": "PatientZero-watch/1.0 (+https://github.com/AtharvRaotole/FalkorDB)"}
RAW_OSV = Path(__file__).resolve().parents[3] / "data" / "raw" / "osv"


class FetchError(RuntimeError):
    pass


def _get_capped(url: str, cap: int, headers: dict | None = None, timeout: float = TIMEOUT) -> tuple[int, bytes, dict]:
    """GET with a hard byte cap (the body is streamed and cut at `cap`) and a timeout."""
    with httpx.Client(timeout=httpx.Timeout(timeout), headers=UA, follow_redirects=False) as c:
        with c.stream("GET", url, headers=headers or {}) as r:
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > cap:
                    break
            return r.status_code, bytes(buf[:cap + 1]), dict(r.headers)


@dataclass
class IndexHead:
    status: int
    entries: list[tuple[str, str]]  # (modified, id), newest first, MAL ids only
    total_lines: int
    bytes: int
    etag: str | None
    unchanged: bool = False


def fetch_index_head(etag: str | None = None, nbytes: int = INDEX_BYTES) -> IndexHead:
    headers = {"Range": f"bytes=0-{nbytes - 1}"}
    if etag:
        headers["If-None-Match"] = etag
    status, body, h = _get_capped(INDEX_URL, nbytes, headers)
    if status == 304:
        return IndexHead(304, [], 0, 0, etag, unchanged=True)
    if status not in (200, 206):
        raise FetchError(f"OSV index HTTP {status}")
    text = body[:nbytes].decode("utf-8", "replace")
    lines = text.split("\n")
    if len(body) >= nbytes or status == 206:
        lines = lines[:-1]  # the last line may be cut by the byte range
    entries = []
    for ln in lines:
        ts, _, vid = ln.strip().partition(",")
        if ID_RE.match(vid) and ts:
            entries.append((ts, vid))
    return IndexHead(status, entries, len(lines), min(len(body), nbytes), h.get("etag"))


def fetch_record(vid: str) -> dict:
    if not ID_RE.match(vid):
        raise FetchError(f"not a MAL id: {vid[:40]}")
    status, body, _ = _get_capped(RECORD_URL.format(id=vid), RECORD_MAX_BYTES)
    if status != 200:
        raise FetchError(f"OSV {vid}: HTTP {status}")
    if len(body) > RECORD_MAX_BYTES:
        raise FetchError(f"OSV {vid}: record larger than {RECORD_MAX_BYTES} bytes")
    return json.loads(body)


def fetch_records(ids: list[str], deadline_s: float = 60.0) -> tuple[dict[str, dict], dict[str, str]]:
    """Fetch many records concurrently. Returns (records, errors). Stops collecting at the deadline."""
    out, errs = {}, {}
    t0 = time.monotonic()
    ex = ThreadPoolExecutor(max(1, WORKERS))
    try:
        futs = {ex.submit(fetch_record, i): i for i in ids}
        remaining = max(1.0, deadline_s - (time.monotonic() - t0))
        try:
            for f in as_completed(futs, timeout=remaining):
                i = futs[f]
                try:
                    out[i] = f.result()
                except Exception as e:  # noqa: BLE001
                    errs[i] = f"{type(e).__name__}: {str(e)[:120]}"
        except TimeoutError:
            for f, i in futs.items():
                if i not in out and i not in errs:
                    errs[i] = "deadline"
                    f.cancel()
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return out, errs


@dataclass
class Advisory:
    id: str
    pkg: str
    bad_versions: list[str]
    all_versions: bool
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    published: str | None = None
    modified: str | None = None
    url: str = ""
    other_ranges: list[str] = field(default_factory=list)

    def row(self, ingested_at: str) -> dict:
        ids = [x.upper() for x in [self.id, *self.aliases]]
        return dict(id=self.id, ids=ids, aliases=self.aliases,
                    ghsa=next((a for a in self.aliases if a.upper().startswith("GHSA-")), None),
                    cve=next((a for a in self.aliases if a.upper().startswith("CVE-")), None),
                    summary=self.summary[:300], published=self.published, modified=self.modified, url=self.url,
                    pkg=self.pkg, bad_versions=self.bad_versions[:200],
                    bad_version=(self.bad_versions[-1] if self.bad_versions else "*"),
                    all_versions=self.all_versions, ingested_at=ingested_at)


def normalize(rec: dict) -> list[Advisory]:
    """OSV record -> one Advisory per affected npm package. Withdrawn records yield nothing.

    all_versions: a SEMVER range that starts at 0 with no fix and no explicit version list (the whole package is
    malware). Explicit `versions` win; any other bounded range is kept in other_ranges (checked with intersects)."""
    if rec.get("withdrawn") or not ID_RE.match(str(rec.get("id", ""))):
        return []
    out = []
    for af in rec.get("affected") or []:
        pkg = (af.get("package") or {})
        if pkg.get("ecosystem") != "npm" or not pkg.get("name"):
            continue
        name = str(pkg["name"])[:214]
        versions = [str(v)[:64] for v in (af.get("versions") or [])][:200]
        all_versions, other = False, []
        for rg in af.get("ranges") or []:
            if rg.get("type") not in ("SEMVER", "ECOSYSTEM"):
                continue
            intro, fixed, last = None, None, None
            for ev in rg.get("events") or []:
                intro = ev.get("introduced", intro)
                fixed = ev.get("fixed", fixed)
                last = ev.get("last_affected", last)
            if intro in ("0", "0.0.0") and not fixed and not last and not versions:
                all_versions = True
            elif intro is not None and not versions:
                rng = f">={intro}" + (f" <{fixed}" if fixed else "") + (f" <={last}" if last else "")
                other.append(rng)
        if not versions and not all_versions and not other:
            all_versions = True  # an npm MAL record without version data: treat the package as malware
        out.append(Advisory(id=rec["id"], pkg=name, bad_versions=sorted(set(versions)), all_versions=all_versions,
                            aliases=[str(a)[:40] for a in rec.get("aliases") or []][:10],
                            summary=str(rec.get("summary") or f"Malicious code in {name} (npm)"),
                            published=rec.get("published"), modified=rec.get("modified"),
                            url=f"https://osv.dev/vulnerability/{rec['id']}", other_ranges=other))
    return out


def cached_record(vid: str) -> dict | None:
    """Offline fallback for replays: a committed OSV record under data/raw/osv (by MAL id or alias)."""
    direct = RAW_OSV / f"{vid}.json"
    paths = ([direct] if direct.exists() else []) + sorted(RAW_OSV.glob("MAL-*.json"))
    for p in paths:
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if d.get("id") == vid or vid.upper() in [a.upper() for a in d.get("aliases") or []]:
            return d
    return None
