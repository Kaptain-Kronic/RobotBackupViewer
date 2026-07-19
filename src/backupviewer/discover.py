"""Network discovery that populates the library.

NetworkScanJob mirrors ftpbackup.BackupJob's shape (a uuid `id`, a lock-guarded
progress dict, snapshot()/cancel(), a run() driven on a daemon thread), so the
api layer polls it through the shared scan endpoints: sweep a subnet for FANUC
controllers over FTP and return the reachable ones, best-effort named from the
controller (IP as the fallback).

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

from . import ftpbackup, keyencebackup, mtxbackup, session
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

    def __init__(self):
        self.id = uuid.uuid4().hex
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._p = {
            "id": self.id, "kind": self.kind, "status": "pending",
            "total": 0, "scanned": 0, "found": 0, "current": "",
            "results": [], "error": "",
        }

    def _set(self, **kw):
        with self._lock:
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
        super().__init__()
        self.cidr = cidr
        self.port = int(port or PORT)
        self.smb_port = int(smb_port or SMB_PORT)
        self.port_timeout = port_timeout
        self._ftp_factory = ftp_factory
        self._host_provider = host_provider
        self._port_check = port_check
        self._workers = workers
        self._mtx_mount = mtx_mount or mtxbackup._smb_mount   # SMB mount (injectable for tests)
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

        # --- Matrox cameras: EtherNet/IP identity (vendor 1144) and/or the SMB
        # `mtxuser` share ---
        # A Matrox camera has NO FTP (port 21 closed). The EtherNet/IP broadcast
        # already told us which hosts are Matrox (transport-independent - works
        # even if SMB is off); the SMB probe then confirms the camera is BACKUP-ABLE
        # (mtxuser share + da/) and reads its real name from a saved-image sidecar.
        # A camera known by identity but with no reachable share is still emitted,
        # flagged backup_ready=False, so it shows up for manual handling.
        eip = self._matrox_eip.get(host)
        smb_open = self._port_check(host, self.smb_port, self.port_timeout)
        if eip or smb_open:
            cam = mtxbackup.probe_camera(host, mount=self._mtx_mount) if smb_open else {}
            backup_ready = bool(cam.get("reachable") and (cam.get("has_da") or cam.get("has_images")))
            if eip or backup_ready:   # identity OR a confirmed share => it's a camera
                ident = (mtxbackup.resolve_camera_name(host, mount=self._mtx_mount)
                         if backup_ready else {"name": "", "model": ""})
                return {
                    "host": host, "device_type": "camera-mtx",
                    "name": ident["name"],
                    "model": ident["model"] or (eip.get("product", "") if eip else ""),
                    "serial": (eip.get("serial") if eip else None),
                    "f_number": "", "banner": "",
                    "has_da": bool(cam.get("has_da")), "has_images": bool(cam.get("has_images")),
                    "backup_ready": backup_ready,
                    "via": "eip" if eip else "smb",
                }
        return None


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
