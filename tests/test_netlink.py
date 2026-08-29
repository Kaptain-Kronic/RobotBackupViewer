"""netlink.py - the Windows IP Helper bindings.

The ctypes calls themselves can only be exercised on Windows, so those are one
gated smoke test that asserts the SHAPE of what comes back, never a value: a dev
box's adapters and neighbours differ from a plant laptop's, and freezing a real
reading into an assertion would make this suite machine-dependent.
"""
import sys

import pytest

from backupviewer import netlink


def test_oui_is_the_vendor_half_of_a_mac():
    assert netlink.oui("00:11:22:33:44:55") == "00:11:22"
    assert netlink.oui("") == ""
    assert netlink.oui("00:11") == ""          # too short to name a vendor


def test_adapter_kind_reads_iana_iftypes():
    # ifType is used instead of matching words in a media string because a tunnel
    # reports a number we would never have guessed a name for
    assert netlink.adapter_kind(6) == "ethernet"
    assert netlink.adapter_kind(71) == "wifi"
    assert netlink.adapter_kind(24) == "loopback"
    assert netlink.adapter_kind(53) == "other"


def test_mac_formatting_respects_the_reported_length():
    raw = [0x00, 0xE0, 0x4C, 0x68, 0x25, 0x90, 0xFF, 0xFF]
    assert netlink._mac(raw, 6) == "00:E0:4C:68:25:90"
    assert netlink._mac(raw, 0) == ""
    assert netlink._mac(raw, 99) == netlink._mac(raw, len(raw))   # never overruns


def test_the_state_vocabulary_covers_every_windows_value():
    # NL_NEIGHBOR_STATE is 0..6; a gap would silently render as "unknown"
    assert sorted(netlink.NEIGHBOUR_STATES) == list(range(7))
    assert netlink.LIVE_STATE in netlink.KNOWN_STATES
    # `stale` MUST count as known: a healthy segment sits mostly stale, so
    # treating it as a fault would light the panel up with false alarms
    assert "stale" in netlink.KNOWN_STATES
    assert "unreachable" not in netlink.KNOWN_STATES


def test_send_arp_refuses_junk_without_touching_the_network():
    assert netlink.send_arp("") == ""
    assert netlink.send_arp("not-an-address") == ""


@pytest.mark.skipif(sys.platform != "win32", reason="iphlpapi is Windows-only")
def test_reads_return_the_documented_shape():
    assert netlink.available() is True
    for a in netlink.adapters():
        assert {"name", "ifindex", "kind", "up", "ip",
                "prefix", "gateway", "mac", "speed"} <= set(a)
        assert isinstance(a["up"], bool)
    for n in netlink.neighbours():
        assert {"ip", "mac", "state", "ifindex", "reach_ms"} <= set(n)
        assert n["state"] in set(netlink.NEIGHBOUR_STATES.values()) | {"unknown"}


def test_every_read_degrades_instead_of_raising(monkeypatch):
    """A non-Windows box, or a frozen-exe quirk, must downgrade the feature
    rather than break app boot."""
    monkeypatch.setattr(netlink, "_DLL", None)
    assert netlink.available() is False
    assert netlink.adapters() == []
    assert netlink.neighbours() == []
    assert netlink.send_arp("192.0.2.1") == ""
