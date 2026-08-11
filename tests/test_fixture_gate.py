"""The absence reporter, plus the gate's own tests.

A clean clone must SAY the private-fixture suite is not running - one visible
line - instead of quietly skipping it (see fixtureutil.py for the ten-day
incident this exists for). And the gate mechanism itself is tested here with
synthetic paths, so the thing that guards against silent rot cannot silently
rot."""
import pytest

import fixtureutil


def test_private_fixture_tree_presence():
    n = fixtureutil.backup_root_count()
    if n == 0:
        if fixtureutil.fixtures_expected():
            pytest.fail(
                "private SampleBackup tree expected on this machine but holds "
                "0 backup roots - the private parser suite is silently not "
                "running. (BV_REQUIRE_SAMPLE=0 silences this while the tree "
                "is deliberately unplugged.)")
        pytest.skip("private SampleBackup fixture: 0 backup roots present - "
                    "the real-backup parser suite is not running here "
                    "(expected on a clean clone)")
    assert n > 0


def test_gate_returns_present_dir(tmp_path):
    assert fixtureutil.require_backup(tmp_path, "x") == tmp_path


def test_gate_errors_when_expected(monkeypatch, tmp_path):
    """A dead pin on a machine that should have the tree is an ERROR - the
    exact failure that once hid as 74 green-looking skips."""
    monkeypatch.setenv("BV_REQUIRE_SAMPLE", "1")
    with pytest.raises(pytest.fail.Exception, match="private fixture missing"):
        fixtureutil.require_backup(tmp_path / "gone", "dead pin")


def test_gate_skips_when_not_expected(monkeypatch, tmp_path):
    monkeypatch.setenv("BV_REQUIRE_SAMPLE", "0")
    with pytest.raises(pytest.skip.Exception, match="not present"):
        fixtureutil.require_backup(tmp_path / "gone", "clean clone")


def test_expectation_follows_conftest_presence(monkeypatch, tmp_path):
    """With no env override, the excluded conftest's existence is the signal
    that this machine should hold the tree."""
    monkeypatch.delenv("BV_REQUIRE_SAMPLE", raising=False)
    ghost = tmp_path / "conftest.py"
    monkeypatch.setattr(fixtureutil, "_CONFTEST", ghost)
    assert fixtureutil.fixtures_expected() is False
    ghost.write_text("# present", encoding="utf-8")
    assert fixtureutil.fixtures_expected() is True
    # and the env var overrides in both directions
    monkeypatch.setenv("BV_REQUIRE_SAMPLE", "0")
    assert fixtureutil.fixtures_expected() is False
    monkeypatch.setenv("BV_REQUIRE_SAMPLE", "1")
    monkeypatch.setattr(fixtureutil, "_CONFTEST", tmp_path / "nope")
    assert fixtureutil.fixtures_expected() is True
