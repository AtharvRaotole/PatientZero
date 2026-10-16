"""Network inputs for BYO scans: GitHub (package.json, package-lock.json) and deps.dev, with a file cache.

Cache layout (data/raw/byo/):
  github/<owner>__<repo>/{package.json,package-lock.json,meta.json}   refreshed after GITHUB_TTL
  versions/<pkg>.json      deps.dev GetPackage (version list + publishedAt), refreshed after VERSIONS_TTL
  depsdev/<pkg>@<ver>.json deps.dev :dependencies for an exact version (immutable, cached forever)
The committed fleet cache data/raw/depsdev/ is read first, so the demo fleet's packages cost nothing.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from functools import cmp_to_key
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import nodesemver

from .. import config

RAW = config.ROOT / "data" / "raw"
CACHE = RAW / "byo"
GITHUB_TTL = 3600
VERSIONS_TTL = 24 * 3600
HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_PACKAGE_JSON = 200_000          # bytes
MAX_LOCKFILE = 8_000_000            # bytes
UA = {"User-Agent": "patientzero-byo/1.0 (+https://github.com/AtharvRaotole/FalkorDB)"}

GITHUB_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100}?)(?:\.git)?(?:/(?:tree|blob)/(?P<ref>[^/?#]+))?/?(?:[?#].*)?$")
SHORT_RE = re.compile(r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/(?P<repo>[A-Za-z0-9._-]{1,100})$")


class ByoError(Exception):
    """A user-facing error with an HTTP status."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def parse_github_url(url: str) -> tuple[str, str, str | None]:
    url = (url or "").strip()
    m = GITHUB_RE.match(url) or SHORT_RE.match(url)
    if not m:
        raise ByoError(400, "bad_github_url",
                       "github_url must look like https://github.com/<owner>/<repo> (public repos only)")
    repo = m.group("repo")
    if repo.endswith(".git"):
        repo = repo[:-4]
    ref = m.groupdict().get("ref")
    return m.group("owner"), repo, ref


def _client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT, headers=UA, follow_redirects=True)


def _fresh(p: Path, ttl: float) -> bool:
    return p.exists() and (time.time() - p.stat().st_mtime) < ttl


def _get_limited(c: httpx.Client, url: str, limit: int) -> bytes | None:
    """GET with a hard size cap. Returns None on 404."""
    with c.stream("GET", url) as r:
        if r.status_code == 404:
            return None
        if r.status_code == 429 or r.status_code == 403:
            raise ByoError(502, "upstream_rate_limited", f"GitHub refused the request ({r.status_code}). Try again later.")
        r.raise_for_status()
        buf = bytearray()
        for chunk in r.iter_bytes():
            buf.extend(chunk)
            if len(buf) > limit:
                raise ByoError(413, "file_too_large", f"{url.rsplit('/', 1)[-1]} is larger than {limit // 1000} KB")
        return bytes(buf)


@dataclass
class RepoFiles:
    owner: str
    repo: str
    ref: str
    package_json: dict
    lockfile: dict | None = None
    lockfile_skipped: str | None = None
    cached: bool = False


def fetch_github(url: str) -> RepoFiles:
    owner, repo, ref = parse_github_url(url)
    ref = ref or "HEAD"   # raw.githubusercontent.com resolves HEAD to the default branch
    d = CACHE / "github" / f"{owner}__{repo}__{re.sub(r'[^A-Za-z0-9._-]', '_', ref)}"
    pj, lj, meta = d / "package.json", d / "package-lock.json", d / "meta.json"
    if _fresh(meta, GITHUB_TTL):
        m = json.loads(meta.read_text())
        return RepoFiles(owner, repo, m["ref"], json.loads(pj.read_text()),
                         json.loads(lj.read_text()) if lj.exists() else None, m.get("lockfile_skipped"), True)
    base = f"https://raw.githubusercontent.com/{owner}/{repo}/{urllib.parse.quote(ref, safe='')}"
    try:
        with _client() as c:
            raw = _get_limited(c, f"{base}/package.json", MAX_PACKAGE_JSON)
            if raw is None:
                raise ByoError(404, "package_json_not_found",
                               f"No package.json at the root of github.com/{owner}/{repo} ({ref}). "
                               "The repo may be private, missing, or not a JS project. Monorepos: only the root is read.")
            lock, skipped = None, None
            try:
                lraw = _get_limited(c, f"{base}/package-lock.json", MAX_LOCKFILE)
                lock = json.loads(lraw) if lraw else None
            except ByoError as e:
                if e.code != "file_too_large":
                    raise
                skipped = e.message
            except ValueError:
                skipped = "package-lock.json is not valid JSON"
    except (httpx.HTTPError, ByoError) as e:
        if meta.exists() and not (isinstance(e, ByoError) and e.status in (404, 413)):
            m = json.loads(meta.read_text())  # stale cache beats failing the demo
            return RepoFiles(owner, repo, m["ref"], json.loads(pj.read_text()),
                             json.loads(lj.read_text()) if lj.exists() else None, m.get("lockfile_skipped"), True)
        if isinstance(e, ByoError):
            raise
        if isinstance(e, httpx.TimeoutException):
            raise ByoError(504, "upstream_timeout", f"Timed out fetching from GitHub: {e}") from e
        raise ByoError(502, "upstream_error", f"Could not fetch from GitHub: {e}") from e
    pkg = parse_package_json(raw.decode("utf-8", "replace"))
    d.mkdir(parents=True, exist_ok=True)
    pj.write_text(json.dumps(pkg, indent=1))
    if lock is not None:
        lj.write_text(json.dumps(lock))
    elif lj.exists():
        lj.unlink()
    meta.write_text(json.dumps({"owner": owner, "repo": repo, "ref": ref, "fetched_at": time.time(),
                                "lockfile_skipped": skipped}))
    return RepoFiles(owner, repo, ref, pkg, lock, skipped, False)


def parse_package_json(text: str) -> dict:
    if len(text.encode("utf-8")) > MAX_PACKAGE_JSON:
        raise ByoError(413, "file_too_large", f"package.json is larger than {MAX_PACKAGE_JSON // 1000} KB")
    try:
        pkg = json.loads(text)
    except ValueError as e:
        raise ByoError(400, "bad_package_json", f"package.json is not valid JSON: {e}") from e
    if not isinstance(pkg, dict):
        raise ByoError(400, "bad_package_json", "package.json must be a JSON object")
    for k in ("dependencies", "devDependencies"):
        if k in pkg and not isinstance(pkg[k], dict):
            raise ByoError(400, "bad_package_json", f"package.json '{k}' must be an object")
    return pkg


# --------------------------------------------------------------------------- deps.dev
def _q(s: str) -> str:
    return urllib.parse.quote(s, safe="")


def _safe(name: str) -> str:
    return name.replace("/", "__")


def _cached_json(paths: list[Path], write: Path, url: str, c: httpx.Client, ttl: float | None = None):
    for p in paths:
        if p.exists() and (ttl is None or _fresh(p, ttl)):
            return json.loads(p.read_text())
    try:
        r = c.get(url)
        if r.status_code == 404:
            data = {"_status": 404}
        else:
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError:
        for p in paths:  # network trouble: a stale cache entry beats failing the scan
            if p.exists():
                return json.loads(p.read_text())
        raise
    write.parent.mkdir(parents=True, exist_ok=True)
    write.write_text(json.dumps(data, sort_keys=True))
    return data


def package_versions(pkg: str, c: httpx.Client) -> dict:
    p = CACHE / "versions" / f"{_safe(pkg)}.json"
    return _cached_json([p], p, f"https://api.deps.dev/v3/systems/npm/packages/{_q(pkg)}", c, VERSIONS_TTL)


def dependencies(pkg: str, ver: str, c: httpx.Client) -> dict:
    name = f"{_safe(pkg)}@{ver}.json"
    w = CACHE / "depsdev" / name
    return _cached_json([RAW / "depsdev" / name, w], w,
                        f"https://api.deps.dev/v3/systems/npm/packages/{_q(pkg)}/versions/{_q(ver)}:dependencies", c)


def candidate_versions(versions: dict, requirement: str, as_of: str, limit: int = 3) -> tuple[list[str], str]:
    """Up to `limit` versions satisfying `requirement`, newest first, preferring ones published before `as_of`."""
    vs = [v for v in (versions.get("versions") or []) if v.get("versionKey", {}).get("version")]
    before = [v["versionKey"]["version"] for v in vs if (v.get("publishedAt") or "") < as_of]
    allv = [v["versionKey"]["version"] for v in vs]
    for pool, note in ((before, f"newest matching version published before {as_of[:10]}"),
                       (allv, "newest matching version (none matched before the attack date)")):
        ok = []
        for v in pool:
            try:
                if nodesemver.satisfies(v, requirement, loose=True):
                    ok.append(v)
            except Exception:  # noqa: BLE001
                continue
        if ok:
            ok.sort(key=cmp_to_key(lambda a, b: nodesemver.compare(a, b, True)), reverse=True)
            return ok[:limit], note
    return [], f"no published version satisfies '{requirement}'"


def pick_version(versions: dict, requirement: str, as_of: str) -> tuple[str | None, str]:
    c, note = candidate_versions(versions, requirement, as_of, 1)
    return (c[0] if c else None), note


@dataclass
class Resolved:
    name: str
    requirement: str
    dev: bool
    version: str | None = None
    note: str = ""
    graph: dict | None = None
    error: str | None = None


NON_SEMVER = ("npm:", "git", "http:", "https:", "file:", "link:", "workspace:", "github:", "portal:", "patch:")


def resolve_direct(deps: list[tuple[str, str, bool]], as_of: str, budget_s: float = 40.0) -> list[Resolved]:
    out = [Resolved(n, r, dev) for n, r, dev in deps]
    deadline = time.time() + budget_s

    def work(x: Resolved):
        req = str(x.requirement).strip()
        if req.startswith(NON_SEMVER) or ("/" in req and not req.startswith("@")):
            x.error = f"'{req}' is not a registry semver range (alias, git, file or workspace); skipped"
            return x
        if time.time() > deadline:
            x.error = "scan time budget exceeded before this dependency was resolved"
            return x
        try:
            with _client() as c:
                vers = package_versions(x.name, c)
                if vers.get("_status") == 404:
                    x.error = "not found on deps.dev (private or unpublished package?)"
                    return x
                cands, x.note = candidate_versions(vers, req if req not in ("", "*", "latest") else "*", as_of)
                if not cands:
                    x.error = x.note
                    return x
                for i, v in enumerate(cands):  # deps.dev may not have analysed the newest release yet
                    g = dependencies(x.name, v, c)
                    if g.get("_status") != 404 and g.get("nodes"):
                        x.version, x.graph = v, g
                        if i:
                            x.note += f"; {cands[0]} not analysed by deps.dev yet, used {v}"
                        break
                else:
                    x.error = f"deps.dev has no dependency graph for {x.name}@{cands[0]}"
        except httpx.TimeoutException:
            x.error = "deps.dev timed out"
        except httpx.HTTPError as e:
            x.error = f"deps.dev error: {e}"
        return x

    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(work, out))


def lockfile_versions(lock: dict | None) -> dict[str, list[str]]:
    """{package: sorted distinct locked versions} from package-lock.json v1, v2 or v3."""
    if not lock:
        return {}
    found: dict[str, set] = {}
    pk = lock.get("packages")
    if isinstance(pk, dict):
        for path, meta in pk.items():
            if not path or not isinstance(meta, dict) or "node_modules/" not in path:
                continue
            name = meta.get("name") or path.rsplit("node_modules/", 1)[-1]
            if meta.get("version"):
                found.setdefault(name, set()).add(str(meta["version"]))
    else:
        def walk(deps):
            for name, meta in (deps or {}).items():
                if isinstance(meta, dict):
                    if meta.get("version"):
                        found.setdefault(name, set()).add(str(meta["version"]))
                    walk(meta.get("dependencies"))
        walk(lock.get("dependencies"))
    return {k: sorted(v) for k, v in found.items()}
