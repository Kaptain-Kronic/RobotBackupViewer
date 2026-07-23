"""Check the public GitHub releases feed for a newer BackupViewer.

One GET to the releases/latest API, compared numerically against the running
__version__. Offline is a first-class outcome, not an error: a single attempt,
a short timeout, no retries, and every failure collapses to an honest status
string the UI shows as-is. check() never raises.

Auto-check policy (should_autocheck): only the packaged exe phones home on
boot — source runs and hidden-window probes stay silent — and the ⚙ toggle
(update_check) can turn even that off. BV_UPDATE_CHECK=1 forces the boot
check on for a source run (testing the real path); =0 forces it off anywhere.
The manual about-box button bypasses the policy and always really checks.

Statuses: update / current / ahead / unknown / norelease / offline / error
(the api endpoint adds "skipped" for a policy'd-out auto check).
"""
from __future__ import annotations

import json
import re
import urllib.request
from urllib.error import HTTPError, URLError

from . import __version__

REPO = "Kaptain-Kronic/RobotBackupViewer"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
TIMEOUT_S = 4.0


def parse_version(text) -> tuple | None:
    """"1.3" / "v1.10" / "1.3.2" -> a comparable int tuple; anything else None.
    Trailing zero parts drop so "1.3.0" and "1.3" compare equal."""
    if not isinstance(text, str):
        return None
    m = re.fullmatch(r"[vV]?(\d+(?:\.\d+)*)", text.strip())
    if not m:
        return None
    parts = [int(p) for p in m.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def classify(current: str, latest: str) -> str:
    """update / current / ahead, or unknown when either tag won't parse."""
    c, l = parse_version(current), parse_version(latest)
    if c is None or l is None:
        return "unknown"
    if l > c:
        return "update"
    if l < c:
        return "ahead"
    return "current"


def fetch_latest(timeout: float = TIMEOUT_S) -> dict:
    """The raw releases/latest JSON. Raises on any trouble; check() maps
    failures to statuses. releases/latest excludes drafts and prereleases,
    which is exactly the "what should a tech be running" question."""
    req = urllib.request.Request(LATEST_URL, headers={
        "User-Agent": f"BackupViewer/{__version__}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - https only
        return json.load(r)


def check(current: str = __version__, fetch=fetch_latest) -> dict:
    """Never raises. -> {status, current, latest?, url?, detail?}."""
    out: dict = {"status": "error", "current": current}
    try:
        rel = fetch()
    except HTTPError as e:  # before URLError: HTTPError subclasses it
        if e.code == 404:
            out["status"] = "norelease"
        else:
            out["status"] = "error"
            out["detail"] = f"github answered {e.code}"
        return out
    except (URLError, OSError):
        out["status"] = "offline"
        return out
    except Exception as e:  # noqa: BLE001 - malformed body, anything else
        out["status"] = "error"
        out["detail"] = type(e).__name__
        return out
    tag = rel.get("tag_name") if isinstance(rel, dict) else None
    if not tag:
        out["status"] = "error"
        out["detail"] = "no tag in the response"
        return out
    out["latest"] = str(tag)
    out["url"] = (rel.get("html_url") or RELEASES_PAGE)
    out["status"] = classify(current, str(tag))
    return out


def should_autocheck(settings_dict: dict, env, frozen: bool) -> bool:
    """The boot-time policy, pure for tests. BV_UPDATE_CHECK=0 wins over
    everything; the ⚙ toggle wins over the =1 force; otherwise only frozen
    exes auto-check."""
    force = (env or {}).get("BV_UPDATE_CHECK", "")
    if force == "0":
        return False
    if (settings_dict or {}).get("update_check") is False:
        return False
    return bool(frozen) or force == "1"
