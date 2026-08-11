"""Tracked half of the private-fixture plumbing.

The fixtures themselves (real plant snapshots under SampleBackup/) and the
conftest.py that pins them are git-excluded - the identifier firewall. That
split once created a blind spot: conftest.py kept pointing at folders a tree
reorg had deleted, and because a missing fixture merely SKIPPED, 74 tests
silently stopped running for ten days while the headline stayed green. This
module is the tracked fix - tracked precisely so the mechanism itself can
never vanish the way the conftest's paths did:

- require_backup() is the one gate every private fixture goes through:
  missing fixture = ERROR on a machine that is supposed to have the tree,
  a visible skip anywhere else (a clean clone legitimately has neither the
  tree nor the conftest).
- "supposed to have it" is auto-detected: the git-excluded conftest.py
  exists next to this file. BV_REQUIRE_SAMPLE=1/0 overrides either way
  (=0 while the tree is deliberately unplugged, =1 to force strictness
  with no conftest around).
- backup_root_count() is runtime discovery for the tracked reporter test
  (test_fixture_gate.py), so a clean clone SAYS "0 private fixtures"
  out loud instead of saying nothing. Only existence and counts are ever
  tracked; which robots the pins resolve to lives in the excluded conftest.
"""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SAMPLE_ROOT = ROOT / "SampleBackup"      # the private library tree (untracked)
_CONFTEST = Path(__file__).with_name("conftest.py")


def fixtures_expected() -> bool:
    """True when a missing private fixture should ERROR instead of skip."""
    v = os.environ.get("BV_REQUIRE_SAMPLE", "")
    if v in ("0", "1"):
        return v == "1"
    return _CONFTEST.exists()


def require_backup(path: Path, label: str) -> Path:
    """Resolve a pinned private backup folder, loudly.

    Present -> the Path. Missing on a machine that should have it -> a test
    ERROR naming the dead pin. Missing anywhere else -> a normal skip.
    """
    path = Path(path)
    if path.is_dir():
        return path
    if fixtures_expected():
        pytest.fail(
            f"private fixture missing: {label} expected at\n  {path}\n"
            "tests/conftest.py exists, so this machine should hold the "
            "private SampleBackup tree. A silent skip here is how 74 tests "
            "once vanished for ten days - re-pin conftest.py (and re-baseline "
            "what the new robot changes), or set BV_REQUIRE_SAMPLE=0 while "
            "the tree is deliberately unplugged.",
            pytrace=False,
        )
    pytest.skip(f"private sample backup not present ({label})")


def backup_root_count() -> int:
    """How many backup roots the private tree holds right now (0 on a clean
    clone). Runtime discovery only - identities never enter tracked files."""
    if not SAMPLE_ROOT.is_dir():
        return 0
    from backupviewer.session import find_backup_roots
    return len(find_backup_roots(SAMPLE_ROOT))
