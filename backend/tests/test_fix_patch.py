"""P1-2: install-safe npm patches (pure functions, no graph)."""
from app.tools import build_fix, major_range, package_json_patch


def P(via, exposed=True):
    return {"via": via, "exposed": exposed, "display": " -> ".join(via)}


def F(pkg, bad, clean, *paths):
    return {"package": pkg, "bad_version": bad, "clean_version": clean, "exposed": True, "advisory_id": "MAL-1",
            "path": paths[0], "alternatives": list(paths[1:])}


def test_major_range():
    assert major_range("6.2.2") == "^6" and major_range("4.4.2") == "^4" and major_range("0.2.3") == "^0.2"


def test_transitive_override_is_scoped_to_compromised_major():
    p = package_json_patch([F("ansi-styles", "6.2.2", "6.2.3", P(["svc", "chalk@5.6.0", "ansi-styles@6.2.1"]))])
    assert p == {"overrides": {"ansi-styles@^6": "6.2.3"}}


def test_direct_dependency_is_bumped_not_overridden():
    p = package_json_patch([F("debug", "4.4.2", "4.4.3", P(["svc", "debug@4.4.1"]),
                              P(["svc", "got@11.8.6", "debug@4.4.1"]))])
    assert p["dependencies"] == {"debug": "^4.4.3"}
    assert p["overrides"] == {"debug@^4": "^4.4.3"}  # same spec as the dependency: no EOVERRIDE


def test_direct_dev_dependency_goes_to_dev_dependencies():
    p = package_json_patch([F("debug", "4.4.2", "4.4.3", P(["svc", "debug@4.4.1"]))], dev_direct={"debug"})
    assert p["devDependencies"] == {"debug": "^4.4.3"} and "dependencies" not in p


def test_build_fix_shapes():
    res = {"exposed": [{"service": "svc", "tier": 1, "team": "t", "exposed_packages": ["debug@4.4.2"],
                        "findings": [F("debug", "4.4.2", "4.4.3", P(["svc", "x@1.0.0", "debug@4.4.1"]))]}]}
    fx = build_fix(res, [])
    assert fx["combined"] == {"overrides": {"debug@^4": "4.4.3"}} and fx["pins"] == {"debug": "4.4.3"}
    s = fx["per_service"][0]
    assert s["pins"] == {"debug": "4.4.3"} and s["overrides"] == s["package_json"] == {"overrides": {"debug@^4": "4.4.3"}}
