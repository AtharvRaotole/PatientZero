import pytest

from app.semver_util import check


@pytest.mark.parametrize("bad,req,expected", [
    ("0.2.3", "^0.2.2", True),        # simple-swizzle: caret on 0.x admits patch
    ("0.3.3", "^0.3.1", True),        # is-arrayish
    ("4.4.2", "^4.3.2", True),        # eslint -> debug
    ("4.4.2", "~4.4.1", True),        # socket.io-adapter -> debug
    ("4.4.2", "~4.3.2", False),       # socket.io -> debug, tilde stays on 4.3.x
    ("4.4.2", "2.6.9", False),        # express pins debug exactly
    ("6.2.1", "^6.2.2", False),       # strip-ansi -> ansi-regex lower bound excludes the bad version
    ("5.6.1", "~5.3.0", False),       # lint-staged -> chalk
    ("10.2.1", "^8.0.0", False),      # jest-worker -> supports-color
    ("4.4.2", "4.x", True),
    ("4.4.2", "4", True),
    ("4.4.2", ">=4.0.0 <5.0.0", True),
    ("4.4.2", "*", True),
    ("4.4.2", "^4.4.3", False),
])
def test_ranges(bad, req, expected):
    ok, reason = check(bad, req, "pkg")
    assert ok is expected
    assert ("admits" in reason) is expected


def test_exact_reason_text_matches_spec():
    ok, reason = check("4.4.2", "2.6.9", "debug")
    assert ok is False
    assert reason == "debug 2.6.9 (exact) does not satisfy 4.4.2"


@pytest.mark.parametrize("req", ["", None, "npm:other@^1.0.0", "github:user/repo", "file:../x"])
def test_unevaluable_ranges_are_not_exposed(req):
    ok, _ = check("1.0.0", req, "pkg")
    assert ok is None
