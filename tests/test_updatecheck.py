"""updatecheck: version parsing/ordering, check()'s honest failure statuses
(every network failure mode -> a status, never an exception), and the boot
auto-check policy. No network anywhere: fetch is injected."""
import urllib.error

import pytest

from backupviewer import updatecheck as uc


# ---- parse_version ---------------------------------------------------------

@pytest.mark.parametrize("text,want", [
    ("1.3", (1, 3)),
    ("v1.4", (1, 4)),
    ("V2.0", (2,)),          # trailing zeros drop
    ("1.3.0", (1, 3)),
    ("1.10", (1, 10)),
    ("1.3.2", (1, 3, 2)),
    (" 1.3 ", (1, 3)),
    ("0", (0,)),
    ("", None),
    ("beta", None),
    ("1.3-rc1", None),
    (None, None),
    (1.3, None),
])
def test_parse_version(text, want):
    assert uc.parse_version(text) == want


# ---- classify --------------------------------------------------------------

@pytest.mark.parametrize("current,latest,want", [
    ("1.3", "1.4", "update"),
    ("1.9", "1.10", "update"),   # numeric, not lexicographic
    ("1.3", "1.3", "current"),
    ("1.3", "v1.3", "current"),
    ("1.3", "1.3.0", "current"),
    ("1.3", "1.0", "ahead"),
    ("junk", "1.3", "unknown"),
    ("1.3", "junk", "unknown"),
])
def test_classify(current, latest, want):
    assert uc.classify(current, latest) == want


# ---- check() ---------------------------------------------------------------

def _release(tag, url="https://example.invalid/rel"):
    return {"tag_name": tag, "html_url": url}


def test_check_newer_release():
    got = uc.check("1.3", fetch=lambda: _release("9.9"))
    assert got["status"] == "update"
    assert got["current"] == "1.3"
    assert got["latest"] == "9.9"
    assert got["url"] == "https://example.invalid/rel"


def test_check_up_to_date():
    assert uc.check("1.3", fetch=lambda: _release("v1.3"))["status"] == "current"


def test_check_ahead_of_latest():
    # a dev/pre-release exe newer than anything published — a real state today
    assert uc.check("1.3", fetch=lambda: _release("1.0"))["status"] == "ahead"


def test_check_unparseable_tag_is_unknown_not_update():
    got = uc.check("1.3", fetch=lambda: _release("release-candidate"))
    assert got["status"] == "unknown"
    assert got["latest"] == "release-candidate"   # still shown, honestly raw


def _raiser(exc):
    def fetch():
        raise exc
    return fetch


def test_check_404_means_no_releases():
    e = urllib.error.HTTPError("https://example.invalid", 404, "nf", None, None)
    assert uc.check("1.3", fetch=_raiser(e))["status"] == "norelease"


def test_check_http_error_is_error_with_code():
    e = urllib.error.HTTPError("https://example.invalid", 500, "boom", None, None)
    got = uc.check("1.3", fetch=_raiser(e))
    assert got["status"] == "error"
    assert "500" in got["detail"]


@pytest.mark.parametrize("exc", [
    urllib.error.URLError("dns says no"),
    TimeoutError("timed out"),
    OSError("network unreachable"),
])
def test_check_network_trouble_is_offline(exc):
    assert uc.check("1.3", fetch=_raiser(exc))["status"] == "offline"


def test_check_garbage_body_is_error():
    assert uc.check("1.3", fetch=_raiser(ValueError("bad json")))["status"] == "error"


def test_check_missing_tag_is_error():
    assert uc.check("1.3", fetch=lambda: {"nope": 1})["status"] == "error"


def test_check_never_raises_and_url_falls_back():
    got = uc.check("1.3", fetch=lambda: {"tag_name": "9.9"})
    assert got["status"] == "update"
    assert got["url"] == uc.RELEASES_PAGE


# ---- should_autocheck ------------------------------------------------------

@pytest.mark.parametrize("settings,env,frozen,want", [
    ({}, {}, True, True),                                # packaged exe: on by default
    ({}, {}, False, False),                              # source run / probe: silent
    ({"update_check": False}, {}, True, False),          # ⚙ toggle wins
    ({"update_check": True}, {}, False, False),          # toggle can't force dev runs on
    ({}, {"BV_UPDATE_CHECK": "1"}, False, True),         # env force-on for testing
    ({}, {"BV_UPDATE_CHECK": "0"}, True, False),         # env force-off wins over frozen
    ({"update_check": False}, {"BV_UPDATE_CHECK": "1"}, False, False),  # toggle beats force-on
])
def test_should_autocheck(settings, env, frozen, want):
    assert uc.should_autocheck(settings, env, frozen) is want
