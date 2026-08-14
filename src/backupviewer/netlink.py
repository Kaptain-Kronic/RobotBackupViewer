"""Windows IP Helper bindings — what the OS already knows about our own link.

This is the *reading* half of "am I actually plugged into the plant switch":
adapter link state, IPv4 + prefix, default gateway, and the neighbour (ARP)
table, all straight out of `iphlpapi.dll` via ctypes. No subprocess, no new
dependency — the same native-call approach `mtxbackup.py` already uses for
`mpr.dll`.

Why not PowerShell, which `discover.list_adapters` uses today: measured on a
plant laptop, `Get-NetAdapter` costs 1609 ms, `Get-NetIPAddress` 1556 ms and
`Get-NetIPConfiguration` 3784 ms, plus ~360 ms of process spawn. The two calls
below cost **12 ms together**. That is the whole reason a status indicator can
poll at all; the PowerShell path is only affordable once, when a dialog opens.

Everything here is a pure read that sends nothing on the wire, with one
exception: `send_arp` deliberately emits a single ARP request. That is the
gentlest reachability probe available on a local segment — it is layer 2 only
and touches no application port on the device, unlike a TCP connect to a
camera's FTP or SMB port.

Policy lives in `discover.py`; this module only reports what Windows says.
Every entry point degrades to empty/false rather than raising, so a non-Windows
dev box or a frozen-exe quirk downgrades the feature instead of breaking boot.
"""
from __future__ import annotations

import ctypes
import logging
import socket
import struct

log = logging.getLogger(__name__)

AF_INET = 2

# GetAdaptersAddresses flags: gateways are the point, and skipping the address
# families we never read keeps the returned buffer small.
_GAA_SKIP_ANYCAST = 0x0002
_GAA_SKIP_MULTICAST = 0x0004
_GAA_SKIP_DNS_SERVER = 0x0008
_GAA_INCLUDE_GATEWAYS = 0x0080
_GAA_FLAGS = (_GAA_INCLUDE_GATEWAYS | _GAA_SKIP_ANYCAST
              | _GAA_SKIP_MULTICAST | _GAA_SKIP_DNS_SERVER)

_ERROR_BUFFER_OVERFLOW = 111
_IF_OPER_STATUS_UP = 1
_INITIAL_BUF = 15000          # MSDN's own suggested starting size

# IANA ifType. Far more reliable than matching words in PhysicalMediaType text:
# a tunnel adapter reports 53 (propVirtual) and never a media string we'd guess.
_IFTYPE_ETHERNET = 6
_IFTYPE_LOOPBACK = 24
_IFTYPE_WIFI = 71

# NL_NEIGHBOR_STATE. The distinction that matters for the UI: `Stale` means "not
# confirmed recently", NOT "unreachable" — a healthy segment sits mostly Stale,
# so painting it as a fault would be a false alarm.
NEIGHBOUR_STATES = {
    0: "unreachable", 1: "incomplete", 2: "probe",
    3: "delay", 4: "stale", 5: "reachable", 6: "permanent",
}
#: states that prove the device answered us at some point
KNOWN_STATES = frozenset({"probe", "delay", "stale", "reachable", "permanent"})
#: the only state that proves it answered *just now*
LIVE_STATE = "reachable"


def _load():
    try:
        dll = ctypes.WinDLL("iphlpapi.dll")
    except (OSError, AttributeError):   # not Windows, or no such library
        return None
    dll.GetAdaptersAddresses.restype = ctypes.c_ulong
    dll.GetIpNetTable2.restype = ctypes.c_ulong
    dll.FreeMibTable.restype = None
    dll.SendARP.restype = ctypes.c_ulong
    return dll


_DLL = _load()


def available() -> bool:
    """False on a non-Windows box, where every read below returns empty."""
    return _DLL is not None


# -- structures ------------------------------------------------------------------
# Each leading `Length`+sibling pair is the documented union with a ULONGLONG,
# so the two 32-bit fields together carry that union's 8-byte alignment.

class _SOCKADDR(ctypes.Structure):
    _fields_ = [("sa_family", ctypes.c_ushort), ("sa_data", ctypes.c_ubyte * 26)]


class _SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.POINTER(_SOCKADDR)),
                ("iSockaddrLength", ctypes.c_int)]


class _UNICAST(ctypes.Structure):
    pass


_UNICAST._fields_ = [
    ("Length", ctypes.c_ulong), ("Flags", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_UNICAST)),
    ("Address", _SOCKET_ADDRESS),
    ("PrefixOrigin", ctypes.c_int), ("SuffixOrigin", ctypes.c_int),
    ("DadState", ctypes.c_int),
    ("ValidLifetime", ctypes.c_ulong), ("PreferredLifetime", ctypes.c_ulong),
    ("LeaseLifetime", ctypes.c_ulong), ("OnLinkPrefixLength", ctypes.c_ubyte),
]


class _GATEWAY(ctypes.Structure):
    pass


_GATEWAY._fields_ = [
    ("Length", ctypes.c_ulong), ("Reserved", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_GATEWAY)),
    ("Address", _SOCKET_ADDRESS),
]


class _ADAPTER(ctypes.Structure):
    pass


_ADAPTER._fields_ = [
    ("Length", ctypes.c_ulong), ("IfIndex", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_ADAPTER)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(_UNICAST)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * 8),
    ("PhysicalAddressLength", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong), ("Mtu", ctypes.c_ulong),
    ("IfType", ctypes.c_ulong), ("OperStatus", ctypes.c_int),
    ("Ipv6IfIndex", ctypes.c_ulong), ("ZoneIndices", ctypes.c_ulong * 16),
    ("FirstPrefix", ctypes.c_void_p),
    ("TransmitLinkSpeed", ctypes.c_ulonglong),
    ("ReceiveLinkSpeed", ctypes.c_ulonglong),
    ("FirstWinsServerAddress", ctypes.c_void_p),
    ("FirstGatewayAddress", ctypes.POINTER(_GATEWAY)),
]


class _IPNET_ROW(ctypes.Structure):
    _fields_ = [
        ("Address", ctypes.c_ubyte * 28),        # SOCKADDR_INET union
        ("InterfaceIndex", ctypes.c_ulong),
        ("InterfaceLuid", ctypes.c_ulonglong),
        ("PhysicalAddress", ctypes.c_ubyte * 32),
        ("PhysicalAddressLength", ctypes.c_ulong),
        ("State", ctypes.c_int),
        ("Flags", ctypes.c_ubyte),
        ("ReachabilityTime", ctypes.c_ulong),
    ]


class _IPNET_TABLE(ctypes.Structure):
    _fields_ = [("NumEntries", ctypes.c_ulong), ("Table", _IPNET_ROW * 1)]


# -- helpers ---------------------------------------------------------------------

def _ipv4(sa_ptr) -> str:
    """A SOCKET_ADDRESS -> dotted IPv4, or "" when it isn't AF_INET."""
    if not sa_ptr:
        return ""
    sa = sa_ptr.contents
    if sa.sa_family != AF_INET:
        return ""
    # sockaddr_in is family(2) port(2) addr(4); sa_data starts after the family
    return socket.inet_ntoa(bytes(sa.sa_data[2:6]))


def _mac(raw, length: int) -> str:
    n = max(0, min(int(length), len(raw)))
    return ":".join(f"{b:02X}" for b in raw[:n])


def adapter_kind(iftype: int) -> str:
    if iftype == _IFTYPE_ETHERNET:
        return "ethernet"
    if iftype == _IFTYPE_WIFI:
        return "wifi"
    if iftype == _IFTYPE_LOOPBACK:
        return "loopback"
    return "other"


def oui(mac: str) -> str:
    """The vendor half of a MAC. Used to label a device whose IP we don't know,
    by matching it against OUIs the library has already taught us."""
    parts = (mac or "").split(":")
    return ":".join(parts[:3]) if len(parts) >= 3 else ""


# -- reads -----------------------------------------------------------------------

def adapters() -> list[dict]:
    """Every IPv4 adapter Windows knows, newest state, ~11 ms:
    [{name, ifindex, kind, up, ip, prefix, cidr_hint, gateway, mac, speed}].

    `up` is real link state (OperStatus), which is what answers "is the cable
    actually in?" — an unplugged dongle reports down while still being listed.
    Returns [] rather than raising so a caller can simply show nothing.
    """
    if _DLL is None:
        return []
    size = ctypes.c_ulong(_INITIAL_BUF)
    buf = ctypes.create_string_buffer(size.value)
    rc = _DLL.GetAdaptersAddresses(AF_INET, _GAA_FLAGS, None, buf, ctypes.byref(size))
    if rc == _ERROR_BUFFER_OVERFLOW:
        # the only documented retry: it just told us how much it needs
        buf = ctypes.create_string_buffer(size.value)
        rc = _DLL.GetAdaptersAddresses(AF_INET, _GAA_FLAGS, None, buf, ctypes.byref(size))
    if rc != 0:
        log.debug("GetAdaptersAddresses failed rc=%s", rc)
        return []

    out: list[dict] = []
    node = ctypes.cast(buf, ctypes.POINTER(_ADAPTER))
    while node:
        a = node.contents
        ip, prefix = "", 0
        u = a.FirstUnicastAddress
        while u:
            got = _ipv4(u.contents.Address.lpSockaddr)
            if got:
                ip, prefix = got, int(u.contents.OnLinkPrefixLength)
                break
            u = u.contents.Next
        gateway = ""
        g = a.FirstGatewayAddress
        while g:
            got = _ipv4(g.contents.Address.lpSockaddr)
            if got:
                gateway = got
                break
            g = g.contents.Next
        out.append({
            "name": a.FriendlyName or "?",
            "ifindex": int(a.IfIndex),
            "kind": adapter_kind(int(a.IfType)),
            "up": a.OperStatus == _IF_OPER_STATUS_UP,
            "ip": ip,
            "prefix": prefix,
            "gateway": gateway,
            "mac": _mac(a.PhysicalAddress, a.PhysicalAddressLength),
            "speed": int(a.ReceiveLinkSpeed),
        })
        node = a.Next
    return out


def neighbours(ifindex: int | None = None) -> list[dict]:
    """The IPv4 neighbour (ARP) table, ~1.4 ms:
    [{ip, mac, state, ifindex, reach_ms}].

    This is the free half of the feature: every device the laptop has spoken to
    on this segment, with no packet sent. `reach_ms` is the OS's reachability
    timer — when it advances between two reads, that entry genuinely freshened,
    which is the only honest basis for showing a device as "active right now".
    """
    if _DLL is None:
        return []
    ptr = ctypes.POINTER(_IPNET_TABLE)()
    rc = _DLL.GetIpNetTable2(AF_INET, ctypes.byref(ptr))
    if rc != 0:
        log.debug("GetIpNetTable2 failed rc=%s", rc)
        return []
    try:
        count = int(ptr.contents.NumEntries)
        if count <= 0:
            return []
        rows = ctypes.cast(
            ctypes.byref(ptr.contents, _IPNET_TABLE.Table.offset),
            ctypes.POINTER(_IPNET_ROW * count),
        ).contents
        out: list[dict] = []
        for r in rows:
            if ifindex is not None and int(r.InterfaceIndex) != ifindex:
                continue
            if int.from_bytes(bytes(r.Address[0:2]), "little") != AF_INET:
                continue
            out.append({
                "ip": socket.inet_ntoa(bytes(r.Address[4:8])),
                "mac": _mac(r.PhysicalAddress, r.PhysicalAddressLength),
                "state": NEIGHBOUR_STATES.get(int(r.State), "unknown"),
                "ifindex": int(r.InterfaceIndex),
                "reach_ms": int(r.ReachabilityTime),
            })
        return out
    finally:
        _DLL.FreeMibTable(ptr)


def send_arp(ip: str) -> str:
    """Resolve one address on the local segment, returning its MAC or "".

    The only call in this module that puts a frame on the wire. An ARP request
    is layer 2 and touches no service on the device, so it is safe against live
    equipment in a way a TCP connect to its FTP/SMB port is not. Blocks for
    roughly a second when nothing answers, so callers must bound concurrency.
    Only meaningful for addresses on our own subnet; anything routed resolves
    the gateway's MAC instead and would be a lie.
    """
    if _DLL is None or not ip:
        return ""
    try:
        dest = struct.unpack("<L", socket.inet_aton(ip))[0]
    except OSError:
        return ""
    mac = (ctypes.c_ubyte * 6)()
    length = ctypes.c_ulong(6)
    try:
        rc = _DLL.SendARP(ctypes.c_ulong(dest), ctypes.c_ulong(0),
                          ctypes.byref(mac), ctypes.byref(length))
    except OSError:
        return ""
    return _mac(mac, length.value) if rc == 0 else ""
