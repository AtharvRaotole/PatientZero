import pytest

from app.router import classify, trigger_of
from app.tools import ID_RE, PKGVER_RE, display_path, norm_id


@pytest.mark.parametrize("text,intent", [
    ("maintainer qix was phished", "exposure"),
    ("chalk and debug were compromised, are we exposed?", "exposure"),
    ("debug@4.4.2", "exposure"),
    ("GHSA-4x49-vf9v-38px", "exposure"),
    ("who is our single point of failure?", "single_points_of_failure"),
    ("which maintainer is the biggest risk", "single_points_of_failure"),
    ("why is img-service connected to is-arrayish?", "path_between"),
    ("how does cli-tools depend on chalk", "path_between"),
    ("who owns checkout-api", "who_to_wake"),
    ("who do I wake for realtime-gateway", "who_to_wake"),
    ("who is on-call for payments", "who_to_wake"),
    ("show incident history", "recall"),
])
def test_classify(text, intent):
    assert classify(text) == intent


def test_id_and_pkgver_parsing():
    assert [norm_id(x) for x in ID_RE.findall("see ghsa-4X49-VF9V-38PX and mal-2025-46974, cve-2025-59144")] == [
        "GHSA-4x49-vf9v-38px", "MAL-2025-46974", "CVE-2025-59144"]
    assert [(m.group(1), m.group(2)) for m in PKGVER_RE.finditer("is @babel/core@7.25.2 or debug@4.4.2 bad?")] == [
        ("@babel/core", "7.25.2"), ("debug", "4.4.2")]


def _ent(**kw):
    base = dict(advisories=[], package_versions=[], maintainers=[], services=[], packages=[], unresolved=[])
    base.update(kw)
    return base


def test_trigger_priority():
    assert trigger_of(_ent(maintainers=["qix"], packages=["debug"]))[1:] == ("maintainer", "qix")
    assert trigger_of(_ent(advisories=[{"query": "GHSA-x", "id": "MAL-1"}], maintainers=["qix"]))[1:] == (
        "advisory", "MAL-1")
    assert trigger_of(_ent(package_versions=[{"package": "debug", "version": "4.4.2", "key": "debug@4.4.2"}]))[1:] == (
        "version", "debug@4.4.2")
    assert trigger_of(_ent(packages=["debug", "chalk"]))[1:] == ("packages", "chalk,debug")
    assert trigger_of(_ent()) is None


def test_display_path():
    s = display_path(["img-service", "sharp@0.33.5", "simple-swizzle@0.2.4"], "^0.2.2", "simple-swizzle", "0.2.3", True)
    assert s == "img-service → sharp@0.33.5 → simple-swizzle ^0.2.2 ⚠ 0.2.3"


def test_memory_lookup_label_when_empty():
    from app import db
    if not db.ping().get("graph_loaded"):
        return
    from app.router import ask
    r = ask("chalk@5.6.1", fresh=True)
    ml = [s for s in r["trace"] if s["tool"] == "memory lookup"]
    assert ml and ml[0]["note"] in ("no prior incidents",) or "prior incident" in ml[0]["note"]
