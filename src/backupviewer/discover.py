"""Network discovery that populates the library.

NetworkScanJob mirrors ftpbackup.BackupJob's shape (a uuid `id`, a lock-guarded
progress dict, snapshot()/cancel(), a run() driven on a daemon thread), so the
api layer polls it through the shared scan endpoints: sweep a subnet for FANUC
controllers over FTP and return the reachable ones, best-effort named from the
controller (IP as the fallback).

Also home to the plant-link watch (LinkWatch): a passive read of THIS laptop's
adapter, gateway and neighbour tables that answers "am I actually on the switch?"
without ever contacting the switch. The raw Windows bindings it reads through
live in netlink.py; the policy — which adapter is the plant link, and what the
evidence adds up to — lives here.

Network code lives only here. The job accepts injectable factories so it runs
fully offline under test (see tests/test_discover.py), the same way
ftpbackup.probe_controller takes an ftp_factory.
"""
from __future__ import annotations

import ftplib
import ipaddress
import json
import logging
import socket
import struct
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import ftpbackup, keyencebackup, mtxbackup, netlink, session
from .parsers import summary_dg

log = logging.getLogger(__name__)

PORT = 21
SMB_PORT = 445            # Matrox cameras are a Samba share (no FTP), not port 21
PORT_TIMEOUT = 0.7        # fast TCP pre-check before the heavier FTP probe
SCAN_WORKERS = 48

# EtherNet/IP identity: one broadcast ListIdentity packet (the mechanism RSLinx
# uses) enumerates every industrial device on the subnet at once. Matrox cameras
# answer with the ODVA vendor id 1144 - a transport-independent signal that finds
# a camera even when its file-share port (SMB/FTP) is closed, and is far cheaper
# than SMB-probing all 254 addresses. Live-confirmed: 21 cameras on one /24.
EIP_PORT = 44818
MATROX_VENDOR_ID = 1144
_NAME_LS_TRIES = 8        # report .LS files to sniff for a robot name
# SUMMARY.DG is synthesized on GET; its F Number sits in the first lines, so a
# small prefix is enough (confirmed on a live R-30iB: --diagnose). The robot name
# comes from a report header, not SUMMARY's ($HOSTNAME is far deeper than is worth
# slurping per host), so we don't read the whole file just to name a robot.
NAME_SUMMARY_CAP = 24_000
# report files RETR'd directly to read their "Robot Name <host>" header. The first
# 8 .LS in a controller's MD: listing are alphabetical programs (-bcked*, abortit,
# ...) with no header, so naming MUST target these by name. LOGBOOK first: on a
# live R-30iB ERRALL.LS reset the data connection while LOGBOOK.LS read cleanly.
_NAME_REPORT_FILES = ("LOGBOOK.LS", "ERRALL.LS", "ERRHIST.LS")


# -- subnet helpers --------------------------------------------------------------

def local_ipv4() -> str:
    """The primary egress IPv4 (sends nothing - just inspects the routing pick)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def default_cidr() -> str:
    """The local /24 to prefill the discover dialog, or "" if undeterminable."""
    ip = local_ipv4()
    if not ip:
        return ""
    try:
        return str(ipaddress.ip_network(ip + "/24", strict=False))
    except ValueError:
        return ""


def normalize_cidr(text: str) -> str:
    """Meet users where they are: a bare IP ("192.0.2.5") means its /24 —
    nobody on the shop floor should need to know CIDR notation. Anything with a
    slash passes through; host bits are tolerated (strict=False downstream)."""
    s = (text or "").strip()
    if not s or "/" in s:
        return s
    try:
        ipaddress.ip_address(s)
    except ValueError:
        return s                                   # not an IP - let validation speak
    return s + "/24"


def enumerate_hosts(cidr: str) -> list[str]:
    """Usable host addresses in a CIDR. A /32 (or /31) yields its literal address(es)."""
    net = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(h) for h in net.hosts()]
    if not hosts:
        hosts = [str(net.network_address)]
    return hosts


# -- network adapters (for the discover dialog) ----------------------------------

# absolute path: PATH is unreliable inside a frozen onefile exe
_PS_EXE = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
_ADAPTER_TIMEOUT = 5
_ADAPTER_PS = (
    "$a=Get-NetAdapter -ErrorAction SilentlyContinue|"
    "Select-Object Name,Status,PhysicalMediaType,ifIndex;"
    "$p=Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue|"
    "Select-Object IPAddress,PrefixLength,ifIndex;"
    "$o=foreach($x in $a){"
    "$m=$p|Where-Object{$_.ifIndex -eq $x.ifIndex}|Select-Object -First 1;"
    "[pscustomobject]@{name=$x.Name;status=[string]$x.Status;"
    "media=[string]$x.PhysicalMediaType;ip=$m.IPAddress;prefix=$m.PrefixLength}};"
    "$o|ConvertTo-Json -Compress"
)


def _powershell(script: str, runner) -> str:
    """Run a PowerShell one-liner and return stdout, or "" on any failure. Uses an
    absolute exe + CREATE_NO_WINDOW so a windowed exe never flashes a console."""
    exe = _PS_EXE if Path(_PS_EXE).exists() else "powershell.exe"
    kwargs = {"capture_output": True, "text": True, "timeout": _ADAPTER_TIMEOUT}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if flags:
        kwargs["creationflags"] = flags
    try:
        proc = runner([exe, "-NoProfile", "-NonInteractive", "-Command", script], **kwargs)
    except Exception:  # noqa: BLE001 - no powershell / timeout / frozen quirk
        return ""
    return getattr(proc, "stdout", "") or ""


def _adapter_kind(media: str) -> str:
    m = (media or "").lower()
    if "802.11" in m or "wireless" in m or "wi-fi" in m:
        return "wifi"
    if "802.3" in m or "ethernet" in m:
        return "ethernet"
    return "other"


def _mark_default_adapter(adapters: list[dict], egress: str) -> None:
    """Default = the up ethernet on the egress IP, else first up ethernet, else
    first up adapter, else nothing."""
    ups = [a for a in adapters if a["up"]]
    eths = [a for a in ups if a["kind"] == "ethernet"]
    chosen = None
    for a in eths:
        if egress and a["ip"] == egress:
            chosen = a
            break
    chosen = chosen or (eths[0] if eths else (ups[0] if ups else None))
    if chosen:
        chosen["default"] = True


def list_adapters(runner=subprocess.run) -> list[dict]:
    """Active network adapters with IPv4 + CIDR for the discover dialog:
    [{name, kind:'ethernet'|'wifi'|'other', ip, cidr, up, default}], ethernet
    first. Windows-only (PowerShell); returns [] on any failure so the caller
    falls back to the local /24. `runner` is injectable so tests never spawn."""
    raw = _powershell(_ADAPTER_PS, runner)
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    egress = local_ipv4()
    out: list[dict] = []
    for d in data if isinstance(data, list) else []:
        ip = str(d.get("ip") or "").strip()
        if not ip or ip.startswith(("169.254.", "127.")):
            continue
        prefix = d.get("prefix")
        try:
            cidr = str(ipaddress.ip_network(f"{ip}/{int(prefix)}", strict=False)) if prefix else ""
        except (ValueError, TypeError):
            cidr = ""
        out.append({
            "name": str(d.get("name") or "?"),
            "kind": _adapter_kind(d.get("media")),
            "ip": ip,
            "cidr": cidr,
            "up": str(d.get("status") or "").lower() == "up",
            "default": False,
        })
    out.sort(key=lambda a: ({"ethernet": 0, "wifi": 1}.get(a["kind"], 2), not a["up"], a["name"].lower()))
    _mark_default_adapter(out, egress)
    return out


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    """Fast TCP pre-check so a /24 sweep doesn't wait on the FTP timeout per host."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# -- plant link watch ------------------------------------------------------------
# "Am I actually plugged into the plant switch?" answered from THIS laptop only:
# adapter, gateway and neighbour tables the OS already maintains (netlink.py).
# The switch is never contacted - no SSH, no SNMP, no management plane at all.
#
# Every published state comes from ONE netlink read, which costs ~11 ms, so there
# is no snapshot cache and no cheap-vs-full tiering to get out of step: each tick
# is a fresh, self-consistent instant.

#: every state the pill can show, worst first. `unknown` is a real answer - it
#: means the read itself failed, and it must never be rendered as ok or as a fault.
LINK_STATES = ("unknown", "no-adapter", "no-link", "no-ip", "no-gateway", "ok")

#: a worse state must be seen this many times running before it is published, so a
#: single dropped sample can't flash a false alarm. Good news needs no such proof.
DOWNGRADE_SAMPLES = 2
#: the gateway is normally in the neighbour table already; when it isn't, resolve
#: it at most this often - an unanswered ARP blocks for about a second.
GATEWAY_ARP_EVERY = 5.0
#: every reason `choose_adapter` / `LinkWatch` can give for its choice. The
#: panel's "chosen" row turns each into words, and the probe holds the two lists
#: equal - a reason added here without words there used to reach the screen as
#: a raw slug (`pinned-missing` did, for weeks).
LINK_WHYS = ("pinned", "pinned-missing", "library", "neighbours", "remembered",
             "gone", "none", "unread")


def _in_network(ip: str, cidr: str) -> bool:
    if not ip or not cidr:
        return False
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def adapter_cidr(adapter: dict) -> str:
    """The adapter's own subnet in CIDR form, or "" when it has no usable IPv4."""
    ip, prefix = adapter.get("ip") or "", adapter.get("prefix") or 0
    if not ip or not prefix:
        return ""
    try:
        return str(ipaddress.ip_network(f"{ip}/{int(prefix)}", strict=False))
    except (ValueError, TypeError):
        return ""


def is_apipa(ip: str) -> bool:
    """A 169.254 address means DHCP never answered - link is up, network isn't."""
    return bool(ip) and ip.startswith("169.254.")


def pack_ips(ips) -> tuple[int, ...]:
    """Addresses as packed integers, skipping anything unparseable.

    choose_adapter counts library addresses per adapter on every tick, and a
    plant library holds thousands. Doing this once at the caller and comparing
    plain ints is the difference between a 10 ms sample and an 80 ms one.
    """
    out = []
    for ip in ips:
        try:
            out.append(int(ipaddress.ip_address(ip)))
        except ValueError:
            continue
    return tuple(out)


def segment_neighbours(neighbours: list[dict], adapter: dict) -> list[dict]:
    """Real unicast neighbours on this adapter's own subnet.

    Drops the noise Windows keeps alongside them: multicast and broadcast pseudo
    entries are permanent fixtures of the table, not devices on the switch.
    """
    cidr = adapter_cidr(adapter)
    ifindex = adapter.get("ifindex")
    out = []
    for n in neighbours:
        ip, mac = n.get("ip") or "", (n.get("mac") or "").upper()
        if n.get("ifindex") != ifindex or not _in_network(ip, cidr):
            continue
        if mac.startswith("01:00:5E") or mac in ("", "FF:FF:FF:FF:FF:FF"):
            continue
        if ip.endswith(".255") or ip == "0.0.0.0":
            continue
        out.append(n)
    return out


def choose_adapter(adapters: list[dict], neighbours: list[dict], *,
                   pin: dict | None = None,
                   library_ips=()) -> tuple[dict | None, str]:
    """Which adapter is the plant link -> (adapter | None, why).

    `library_ips` is packed integers (see pack_ips), cached by the caller.

    The ordering exists to stop one specific lie. A laptop on wi-fi (or a phone
    hotspot) with the dongle unplugged still has a perfectly good internet
    connection, and calling that "connected" would answer a question nobody
    asked. So only two things may name an adapter the plant link: the user
    pinning it, or the library's own devices being on that subnet. Wi-fi is
    never promoted on a hunch - with no evidence we say so and ask.

    A pinned adapter wins even when it is down: that is how an unplugged dongle
    reports `no link` instead of silently hopping to wi-fi.
    """
    usable = [a for a in adapters if a.get("kind") != "loopback"]
    if pin:
        mac = (pin.get("mac") or "").upper()
        name = pin.get("name") or ""
        for a in usable:
            if mac and (a.get("mac") or "").upper() == mac:
                return a, "pinned"
        for a in usable:
            if name and a.get("name") == name:
                return a, "pinned"
        # USB adapters usually vanish from the table when unplugged rather than
        # reporting down, so a missing pin IS the unplugged case - say that,
        # rather than re-picking and pretending nothing happened.
        return None, "pinned-missing"

    live = [a for a in usable if a.get("up") and a.get("ip") and not is_apipa(a["ip"])]
    best, best_hits = None, 0
    for a in live:
        cidr = adapter_cidr(a)
        if not cidr:
            continue
        net = ipaddress.ip_network(cidr, strict=False)
        lo, hi = int(net.network_address), int(net.broadcast_address)
        hits = sum(1 for v in library_ips if lo <= v <= hi)
        if hits > best_hits:
            best, best_hits = a, hits
    if best:
        return best, "library"

    # Nothing in the library to go on (a fresh install). An ethernet port with
    # devices answering on it is the next best evidence - a plant switch port
    # sees a crowd, a tunnel sees nobody.
    best, best_hits = None, 0
    for a in live:
        if a.get("kind") != "ethernet":
            continue
        hits = len(segment_neighbours(neighbours, a))
        if hits > best_hits:
            best, best_hits = a, hits
    if best:
        return best, "neighbours"
    return None, "none"


def classify_state(adapter: dict | None, why: str, neighbours: list[dict], *,
                   arp_fn=None, missing: str = "") -> tuple[str, str]:
    """The link ladder -> (state, detail). Pure apart from the optional gateway
    ARP, which is injectable so tests never touch a network.

    `detail` is the whole point: each rung names the next thing to check, and
    two different faults that share a rung get two different sentences.
    `missing` names the adapter that should have been there (the pin, or the
    one remembered from a moment ago) when `adapter` is None.
    """
    if adapter is None:
        if why == "pinned-missing":
            # The tables cannot tell a dongle that fell out from a pin left over
            # from another dock, PC or VM - both simply match nothing. Naming the
            # pin and both causes is what lets someone stop waiting on a ghost.
            who = missing or "the pinned adapter"
            return ("no-link",
                    f"{who} is pinned but not present — unplugged, or pinned on another PC")
        if why == "gone":
            return "no-link", f"{missing or 'the plant adapter'} is gone — dongle unplugged?"
        return "no-adapter", "no adapter looks like the plant link — pick one"
    name = adapter.get("name") or "?"
    if not adapter.get("up"):
        return "no-link", f"{name}: no link — cable, dongle, or switch port"
    ip = adapter.get("ip") or ""
    if not ip or is_apipa(ip):
        return ("no-ip", f"{name}: link is up but DHCP never answered")
    gateway = adapter.get("gateway") or ""
    if not gateway:
        return ("no-gateway", f"{name}: no gateway is configured on this adapter")

    entry = next((n for n in neighbours
                  if n.get("ip") == gateway and n.get("ifindex") == adapter.get("ifindex")),
                 None)
    state = (entry or {}).get("state", "")
    if entry is None and arp_fn is not None:
        # never spoken to yet - one ARP settles it rather than guessing
        state = "reachable" if arp_fn(gateway) else "unreachable"
    if state in netlink.KNOWN_STATES:
        if state == netlink.LIVE_STATE:
            return "ok", f"gateway {gateway} answering"
        # A resolved MAC parks in `stale` once nothing needs it. That is a quiet
        # network, not a broken one - calling it a fault would be the first lie
        # this feature tells.
        return "ok", f"gateway {gateway} known (idle — nothing has needed it)"
    if not state:
        return "no-gateway", f"gateway {gateway} has not been seen yet"
    return "no-gateway", f"gateway {gateway} is not answering"


def link_adapter_choices(library_ips=(), adapters_fn=None,
                         neighbours_fn=None) -> list[dict]:
    """Every adapter the user could pin, with the evidence behind each:
    [{name, mac, kind, up, ip, cidr, library, neighbours}].

    The picker shows its reasoning rather than asking for blind faith - "17
    library devices on this one" is why a tech can trust the automatic choice,
    and is what lets them correct it when the heuristic guesses wrong.
    """
    adapters = (adapters_fn or netlink.adapters)()
    neighbours = (neighbours_fn or netlink.neighbours)()
    out = []
    for a in adapters:
        if a.get("kind") == "loopback":
            continue
        cidr = adapter_cidr(a)
        hits = 0
        if cidr:
            net = ipaddress.ip_network(cidr, strict=False)
            lo, hi = int(net.network_address), int(net.broadcast_address)
            hits = sum(1 for v in library_ips if lo <= v <= hi)
        out.append({
            "name": a.get("name") or "?", "mac": a.get("mac") or "",
            "kind": a.get("kind") or "other", "up": bool(a.get("up")),
            "ip": a.get("ip") or "", "cidr": cidr,
            "library": hits, "neighbours": len(segment_neighbours(neighbours, a)),
        })
    out.sort(key=lambda a: (not a["up"], -a["library"], -a["neighbours"], a["name"]))
    return out


class LinkWatch:
    """Turns a stream of samples into a published state.

    Holds the only mutable state in the feature: the last published verdict, how
    long it has held, and how many consecutive samples are voting to make things
    worse. Downgrades must win `DOWNGRADE_SAMPLES` in a row before they show, so
    one unlucky read cannot flash a false alarm; upgrades publish at once,
    because good news cannot raise one.
    """

    def __init__(self):
        self.state = "unknown"
        self.detail = ""
        self.since = 0.0
        self._pending = 0
        self._last_arp = 0.0
        self._known = None      # (mac, name) of the adapter we last settled on

    def sample(self, *, pin=None, library_ips=(), now=None,
               adapters_fn=None, neighbours_fn=None, arp_fn=None) -> dict:
        now = time.monotonic() if now is None else now
        adapters_fn = adapters_fn or netlink.adapters
        neighbours_fn = neighbours_fn or netlink.neighbours

        adapters = adapters_fn()
        if not adapters:
            # The read itself failed (no iphlpapi, or a frozen-exe quirk). Say so
            # and hold the last verdict rather than inventing a new one - a tool
            # that turns red when IT breaks teaches people to ignore it.
            return self._hold(now, "couldn't read the adapter tables — "
                                   "showing the last reading")
        neighbours = neighbours_fn()
        adapter, why = choose_adapter(adapters, neighbours, pin=pin,
                                      library_ips=library_ips)
        missing = ""      # the adapter that should have been there, by name
        if adapter is None and why == "pinned-missing":
            missing = (pin or {}).get("name") or (pin or {}).get("mac") or ""
        if adapter is None and why == "none" and self._known:
            # We knew which adapter was the plant link a moment ago. Losing its
            # address (unplugged, or DHCP gone) must not read as "nothing here
            # looks like a plant link" - the honest answer is that THIS adapter
            # went away. Without this, the commonest real event shows the most
            # confusing words.
            mac, name = self._known
            prev = next((a for a in adapters
                         if (mac and (a.get("mac") or "").upper() == mac)
                         or (name and a.get("name") == name)), None)
            adapter, why = (prev, "remembered") if prev else (None, "gone")
            if prev is None:
                missing = name or mac
        if adapter is not None:
            self._known = ((adapter.get("mac") or "").upper(), adapter.get("name") or "")
        gate = None
        if arp_fn is not None and now - self._last_arp >= GATEWAY_ARP_EVERY:
            gate = arp_fn
            self._last_arp = now
        state, detail = classify_state(adapter, why, neighbours, arp_fn=gate,
                                       missing=missing)
        return self._publish(state, detail, now, adapter=adapter, why=why,
                             neighbours=neighbours, probe_ok=True)

    def _hold(self, now, detail) -> dict:
        """Report the last verdict, flagged as unverified. Changes nothing: a
        failed read is not evidence, so it may move the state neither way."""
        return {
            "state": self.state, "detail": detail,
            "since_ms": int(max(0.0, now - self.since) * 1000),
            "probe_ok": False, "why": "unread", "adapter": None,
            "cidr": "", "gateway": "", "neighbours": [],
        }

    def _publish(self, state, detail, now, *, adapter, why, neighbours, probe_ok):
        rank = LINK_STATES.index
        if state != self.state:
            worse = rank(state) < rank(self.state)
            if worse and self.state != "unknown":
                self._pending += 1
                if self._pending < DOWNGRADE_SAMPLES:
                    state, detail = self.state, self.detail   # hold the old verdict
                else:
                    self._pending = 0
            else:
                self._pending = 0
            if state != self.state:
                self.state, self.detail, self.since = state, detail, now
        else:
            self._pending = 0
            self.detail = detail

        seg = segment_neighbours(neighbours, adapter) if adapter else []
        return {
            "state": self.state,
            "detail": self.detail,
            "since_ms": int(max(0.0, now - self.since) * 1000),
            "probe_ok": probe_ok,
            "why": why,
            "adapter": adapter,
            "cidr": adapter_cidr(adapter) if adapter else "",
            "gateway": (adapter or {}).get("gateway", ""),
            "neighbours": seg,
        }


# -- EtherNet/IP identity (transport-independent camera discovery) ----------------

def _parse_list_identity(data: bytes) -> dict | None:
    """Pull {vendor, serial, product} out of an EtherNet/IP ListIdentity reply.
    ODVA layout: 24-byte encapsulation header, item count/type/length, protocol
    version, 16-byte socket address, then the CIP Identity object - vendor id at
    byte 48, serial at 58, a length-prefixed product name at 62. Returns None for
    anything too short/odd to be a real reply."""
    if len(data) < 64:
        return None
    try:
        vendor = struct.unpack_from("<H", data, 48)[0]
        serial = struct.unpack_from("<I", data, 58)[0]
        product = ""
        nl = data[62]
        if 0 < nl < 64 and 63 + nl <= len(data):
            product = data[63:63 + nl].decode("ascii", "replace").strip()
        return {"vendor": vendor, "serial": serial, "product": product}
    except (struct.error, IndexError):
        return None


def eip_list_identity(broadcast_ip: str, *, timeout: float = 1.5, port: int = EIP_PORT,
                      sock_factory=None) -> list[dict]:
    """Broadcast one EtherNet/IP ListIdentity (CIP encapsulation command 0x63) and
    collect every responder as {ip, vendor, serial, product}. Read-only - no
    writes to any device (identical to what RSLinx / an industrial browse does).
    Best-effort: returns [] if the network blocks broadcast, so discovery degrades
    to the per-host SMB/FTP probes."""
    req = bytearray(24)
    req[0] = 0x63                                  # ListIdentity
    make = sock_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
    sock = make()
    out: list[dict] = []
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        # send twice: on a busy shop /24 a single ListIdentity broadcast loses a
        # few replies to UDP collisions; a second send catches most stragglers
        sock.sendto(bytes(req), (broadcast_ip, port))
        sock.sendto(bytes(req), (broadcast_ip, port))
        deadline = time.time() + timeout
        seen: set = set()
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                break
            except OSError:
                break
            if addr[0] in seen:
                continue                       # deduped (we sent the request twice)
            info = _parse_list_identity(data)
            if info:
                seen.add(addr[0])
                info["ip"] = addr[0]
                out.append(info)
    except OSError:
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return out


def matrox_hosts(broadcast_ip: str, probe=eip_list_identity) -> dict:
    """{ip: {vendor, serial, product}} for every Matrox camera (vendor 1144) that
    answered the EtherNet/IP broadcast. Never raises - a failed probe just yields
    {}."""
    out: dict = {}
    try:
        for d in probe(broadcast_ip):
            if d.get("vendor") == MATROX_VENDOR_ID and d.get("ip"):
                out[d["ip"]] = d
    except Exception:  # noqa: BLE001 - identity is a nicety; never sink a scan
        log.exception("EtherNet/IP identity probe failed")
    return out


# -- base job --------------------------------------------------------------------

class _ScanJob:
    kind = "scan"

    def __init__(self, label: str = ""):
        self.id = uuid.uuid4().hex
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._p = {
            "id": self.id, "kind": self.kind, "label": label, "status": "pending",
            "total": 0, "scanned": 0, "found": 0, "current": "",
            "results": [], "error": "",
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "finished": "",
        }

    def _set(self, **kw):
        with self._lock:
            # every terminal path stamps `finished` here, so the subclasses'
            # run() loops don't each carry the bookkeeping
            if ftpbackup.is_terminal(kw.get("status")) and not self._p["finished"]:
                kw.setdefault("finished", time.strftime("%Y-%m-%dT%H:%M:%S"))
            self._p.update(kw)

    def _bump(self, current: str = ""):
        with self._lock:
            self._p["scanned"] += 1
            if current:
                self._p["current"] = current

    def _set_results(self, results: list):
        with self._lock:
            self._p["results"] = list(results)
            self._p["found"] = len(results)

    def _add_result(self, r: dict):
        with self._lock:
            self._p["results"].append(r)
            self._p["found"] = len(self._p["results"])

    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self._p)
            s["results"] = list(self._p["results"])
        return s

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()


# (FolderScanJob was removed with the v0.98 files-are-law pivot: backups join
# the library by being copied into the library folder, which the scan/watcher
# picks up - there is no separate bulk-import walk anymore.)

# -- network discovery -----------------------------------------------------------

class _StopRead(Exception):
    """Raised inside a retrlines callback to stop after the header line."""


class NetworkScanJob(_ScanJob):
    """Sweep a subnet for FANUC controllers (FTP), Keyence CV-X cameras (FTP) and
    Matrox cameras (EtherNet/IP identity + SMB)."""
    kind = "network"

    def __init__(self, cidr, *, port=PORT, smb_port=SMB_PORT, port_timeout=PORT_TIMEOUT,
                 ftp_factory=ftplib.FTP, host_provider=enumerate_hosts,
                 port_check=_tcp_open, workers=SCAN_WORKERS, mtx_mount=None, eip_probe=None):
        super().__init__(label="network sweep " + str(cidr))
        self.cidr = cidr
        self.port = int(port or PORT)
        self.smb_port = int(smb_port or SMB_PORT)
        self.port_timeout = port_timeout
        self._ftp_factory = ftp_factory
        self._host_provider = host_provider
        self._port_check = port_check
        self._workers = workers
        self._mtx_mount = mtx_mount or mtxbackup.smb_mount   # SMB mount (injectable for tests)
        self._eip_probe = eip_probe or eip_list_identity      # EtherNet/IP (injectable for tests)
        self._matrox_eip: dict = {}                           # ip -> identity from the broadcast

    def _identity_sweep(self) -> dict:
        """One EtherNet/IP broadcast up front → {ip: identity} for Matrox cameras,
        so a camera is discovered by identity even when its SMB share is closed.
        Best-effort: {} if the CIDR has no broadcast address or the net blocks it."""
        try:
            bcast = str(ipaddress.ip_network(self.cidr, strict=False).broadcast_address)
        except ValueError:
            return {}
        return matrox_hosts(bcast, probe=self._eip_probe)

    def run(self):
        try:
            self._set(status="scanning")
            hosts = self._host_provider(self.cidr)
            self._set(total=len(hosts))
            self._matrox_eip = self._identity_sweep()
            with ThreadPoolExecutor(max_workers=self._workers) as ex:
                futs = {ex.submit(self._scan_host, h): h for h in hosts}
                for fut in as_completed(futs):
                    self._bump(current=futs[fut])
                    if self.cancelled:
                        continue
                    try:
                        r = fut.result()
                    except Exception:  # noqa: BLE001
                        r = None
                    if r:
                        self._add_result(r)
            self._set(status="cancelled" if self.cancelled else "done")
        except Exception as e:  # noqa: BLE001
            log.exception("network scan failed")
            self._set(status="error", error=f"{type(e).__name__}: {e}")

    def _scan_host(self, host: str):
        if self.cancelled:
            return None
        # --- FTP path (port 21): FANUC robots + Keyence CV-X cameras ---
        if self._port_check(host, self.port, self.port_timeout):
            info = ftpbackup.probe_controller(host, port=self.port, ftp_factory=self._ftp_factory)
            banner = info.get("banner", "") or ""
            if info.get("reachable") and ("FANUC" in banner.upper() or info.get("has_md")):
                ident = resolve_robot_name(self._ftp_factory, host, self.port)
                return {
                    "host": host, "device_type": "robot",
                    "name": ident["name"], "model": ident["model"],
                    "f_number": ident["f_number"], "banner": banner,
                    "has_md": bool(info.get("has_md")), "has_fr": bool(info.get("has_fr")),
                }
            # A Keyence CV-X announces itself in the banner and speaks ANONYMOUS
            # FTP - the plain robot probe already reached it; a cv-x/ sighting
            # makes it a camera (not merely a bare ftpd that allows anon).
            if "CV-X" in banner.upper():
                kc = keyencebackup.probe_keyence(host, port=self.port, ftp_factory=self._ftp_factory)
                if kc.get("reachable") and (kc.get("has_cvx") or kc.get("has_setting")):
                    return {
                        "host": host, "device_type": "camera-keyence", "name": "",
                        "model": banner.split("(")[0].replace("220", "").strip() or "CV-X",
                        "f_number": "", "banner": banner,
                        "has_setting": bool(kc.get("has_setting")),
                    }

        # --- Matrox cameras: identified by the EtherNet/IP broadcast (vendor
        # 1144), which is transport-independent and read-only ---
        # A Matrox camera has NO FTP (port 21 closed). We touch a host's SMB
        # share ONLY once the identity broadcast has already named it a Matrox:
        # authenticating mtxuser/Matrox against every open-445 host on a plant
        # subnet (ordinary PCs, HMIs, file servers) would spray failed logons
        # and disturb the tech's own sessions - a discovery scan must stay
        # gentle. A camera identified but with its share closed is still emitted
        # from identity alone (backup_ready=False) for manual handling.
        eip = self._matrox_eip.get(host)
        if not eip:
            return None
        smb_open = self._port_check(host, self.smb_port, self.port_timeout)
        cam = mtxbackup.probe_camera(host, mount=self._mtx_mount) if smb_open else {}
        backup_ready = bool(cam.get("reachable") and (cam.get("has_da") or cam.get("has_images")))
        ident = (mtxbackup.resolve_camera_name(host, mount=self._mtx_mount)
                 if backup_ready else {"name": "", "model": ""})
        return {
            "host": host, "device_type": "camera-mtx",
            "name": ident["name"],
            "model": ident["model"] or eip.get("product", ""),
            "serial": eip.get("serial"),
            "f_number": "", "banner": "",
            "has_da": bool(cam.get("has_da")), "has_images": bool(cam.get("has_images")),
            "backup_ready": backup_ready,
            "via": "smb" if backup_ready else "eip",
        }


# -- live name resolution --------------------------------------------------------
# Mirrors the PROVEN ftpbackup enumeration: tolerate cwd("MD:") failing (some
# controllers root straight at MD:), and never assume nlst() surfaces the
# synthesized report files - RETR them directly. SUMMARY.DG is the primary source
# (name + model + F-number in one GET); the .LS header sniff is the fallback.

def _connect_md(ftp_factory, host, port):
    """Connect, anonymous login, and land where MD: files live. Mirrors
    ftpbackup._cwd_root + its 'MD: is the only device, stay put' tolerance.
    The caller is responsible for quitting the returned ftp."""
    ftp = ftp_factory(timeout=ftpbackup.CONNECT_TIMEOUT)
    ftp.connect(host, port)
    ftp.login("", "")
    for path in ("/", ""):
        try:
            ftp.cwd(path)
            break
        except ftplib.all_errors:
            continue
    try:
        ftp.cwd("MD:")
    except ftplib.all_errors:
        pass  # some controllers root straight at MD: - stay where we are
    return ftp


def _retr_head(ftp, name, cap) -> str:
    """First `cap` bytes of an FTP file decoded cp1252, or "" on any failure.
    Stops the transfer once cap is reached so a live robot isn't slurped dry."""
    buf = bytearray()

    def grab(chunk, _b=buf):
        _b.extend(chunk)
        if len(_b) >= cap:
            raise _StopRead

    try:
        ftp.retrbinary("RETR " + name, grab)
    except _StopRead:
        pass
    except Exception:  # noqa: BLE001 - 550/odd server/fake without retrbinary
        return ""
    return bytes(buf[:cap]).decode("cp1252", errors="replace")


def _name_from_reports(ftp) -> str:
    """Robot name from a report-.LS header ('<file>   Robot Name <host> ...').
    Tries the hardcoded shortlist by RETR even when nlst() hides them, then any
    .LS the listing does surface - first matching header wins."""
    try:
        listed = [n for n in ftp.nlst() if n.upper().endswith(".LS")]
    except ftplib.all_errors:
        listed = []
    seen: set[str] = set()
    candidates: list[str] = []
    for n in list(_NAME_REPORT_FILES) + listed:
        if n.upper() not in seen:
            seen.add(n.upper())
            candidates.append(n)
    for n in candidates[:_NAME_LS_TRIES]:
        first = {"line": ""}

        def grab(line, _f=first):
            _f["line"] = line
            raise _StopRead

        try:
            ftp.retrlines("RETR " + n, grab)
        except _StopRead:
            pass
        except ftplib.all_errors:
            continue
        m = session._REPORT_HEADER.match(first["line"])
        if m:
            return m.group(2)
    return ""


def resolve_robot_name(ftp_factory, host, port) -> dict:
    """Best-effort {name, model, f_number} from a live controller over one gentle
    FTP connection. SUMMARY.DG (synthesized on GET) is primary - it gives all
    three; the report-.LS header sniff is the name fallback. Returns blanks on
    any failure so the caller falls back to the IP - naming never sinks a scan."""
    out = {"name": "", "model": "", "f_number": ""}
    ftp = None
    try:
        ftp = _connect_md(ftp_factory, host, port)
        head = _retr_head(ftp, "SUMMARY.DG", NAME_SUMMARY_CAP)
        if head:
            try:
                ident = summary_dg.parse_summary(head).get("identity") or {}
                out["name"] = ident.get("robot_name", "") or ""
                out["model"] = ident.get("robot_model", "") or ""
                out["f_number"] = ident.get("f_number", "") or ""
            except Exception:  # noqa: BLE001 - a partial SUMMARY must not sink naming
                pass
        if not out["name"]:
            out["name"] = _name_from_reports(ftp)
        return out
    except Exception:  # noqa: BLE001 - name is a nicety; never let it sink discovery
        return out
    finally:
        if ftp is not None:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001
                try:
                    ftp.close()
                except Exception:  # noqa: BLE001
                    pass


# -- live diagnostic -------------------------------------------------------------

_DIAG_NLST_CAP = 200      # directory entries to capture per device
_DIAG_HEAD_LINES = 3      # first lines of each sniffed file


class _DiagStop(Exception):
    """Stop a diag retrlines after enough header lines."""


def _diag_head_lines(ftp, name) -> dict:
    """{ok, lines|error} - the first few lines of an FTP file, read-only."""
    lines: list[str] = []

    def grab(line, _l=lines):
        _l.append(line)
        if len(_l) >= _DIAG_HEAD_LINES:
            raise _DiagStop

    try:
        ftp.retrlines("RETR " + name, grab)
    except _DiagStop:
        pass
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "lines": lines}


def diagnose_controller(host, *, port=PORT, ftp_factory=ftplib.FTP) -> dict:
    """Read-only FTP probe to debug auto-naming against a LIVE robot. Captures the
    banner, how cwd behaves, the raw directory listings, and the first lines of
    the files we sniff for a name - then the resolved name. ZERO writes. The
    summary is also log.info'd as JSON so it lands in app.log on a shop PC with no
    console. Use its output to tune NAME_SUMMARY_CAP and the report shortlist."""
    port = int(port or PORT)
    out: dict = {"host": host, "port": port, "banner": "",
                 "cwd": {}, "nlst": {}, "files": {}, "resolved": {}, "error": ""}
    ftp = None
    try:
        ftp = ftp_factory(timeout=ftpbackup.CONNECT_TIMEOUT)
        ftp.connect(host, port)
        ftp.login("", "")
        try:
            out["banner"] = (ftp.getwelcome() or "").strip()
        except Exception as e:  # noqa: BLE001
            out["banner"] = f"<{type(e).__name__}: {e}>"
        for path in ("/", "", "MD:"):
            try:
                ftp.cwd(path)
                out["cwd"][path or "(empty)"] = "ok"
            except Exception as e:  # noqa: BLE001
                out["cwd"][path or "(empty)"] = f"{type(e).__name__}: {e}"
        for label, path in (("root", "/"), ("MD:", "MD:")):
            try:
                ftp.cwd(path)
            except Exception:  # noqa: BLE001
                pass
            try:
                out["nlst"][label] = list(ftp.nlst())[:_DIAG_NLST_CAP]
            except Exception as e:  # noqa: BLE001
                out["nlst"][label] = f"{type(e).__name__}: {e}"
        try:
            ftp.cwd("MD:")
        except Exception:  # noqa: BLE001
            pass
        listed: list[str] = []
        try:
            listed = [n for n in ftp.nlst() if n.upper().endswith(".LS")][:5]
        except Exception:  # noqa: BLE001
            pass
        seen: set[str] = set()
        for n in ["SUMMARY.DG", *_NAME_REPORT_FILES, *listed]:
            if n.upper() in seen:
                continue
            seen.add(n.upper())
            out["files"][n] = _diag_head_lines(ftp, n)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        if ftp is not None:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001
                try:
                    ftp.close()
                except Exception:  # noqa: BLE001
                    pass
    # resolve on its own fresh connection so a partial probe above still reports it
    try:
        out["resolved"] = resolve_robot_name(ftp_factory, host, port)
    except Exception as e:  # noqa: BLE001
        out["resolved"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        log.info("diagnose_controller %s -> %s", host, json.dumps(out)[:4000])
    except Exception:  # noqa: BLE001
        pass
    return out
