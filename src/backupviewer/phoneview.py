"""Phone live view: hand a phone the camera picture while you're at the lens.

The app (which can reach the camera network) runs a tiny stdlib HTTP server on
the laptop's own interfaces; the phone scans a QR, opens http://<laptop>:<port>
/v/<token>, and gets a black page with nothing but the live frame - the same
/SavedImages/HMIImage.jpg the multicam wall polls. The laptop relays frames,
so the phone never needs a route to the camera VLAN (hotspot/wifi only needs
to reach the laptop).

Posture, deliberately narrow:
- OFF by default; the server exists only while a share is active and dies
  with the app (daemon thread). stop_session() with no token left shuts it.
- Every route is token-gated (secrets.token_urlsafe) and read-only; unknown
  paths are a plain 404 with no reflection of what was asked.
- The camera IP is fixed at session start by the desktop user - nothing a
  phone sends chooses what gets fetched.
- Gentle with the camera: one in-flight fetch per session ever (single
  flight), floor between fetches, shared cache - five phones still cost the
  camera at most one request per MIN_FETCH_GAP. The camera's own HMI page
  polls this file at 1 Hz; we never exceed 2 Hz.
"""
from __future__ import annotations

import html
import logging
import secrets
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

FRAME_PATH = "/SavedImages/HMIImage.jpg"    # the wall-monitor HMI frame (see home.js)
BIND = "0.0.0.0"                            # tests patch to 127.0.0.1
PORT_BASE = 8756
PORT_TRIES = 20
MIN_FETCH_GAP = 0.45                        # floor between camera fetches, seconds
SESSION_TTL = 12 * 3600
MAX_FRAME_BYTES = 8 * 1024 * 1024


def _fetch_frame(ip: str, timeout: float = 3.0) -> bytes:
    url = f"http://{ip}{FRAME_PATH}?t={int(time.time() * 1000)}"
    req = urllib.request.Request(url, headers={"User-Agent": "BackupViewer"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(MAX_FRAME_BYTES)


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows SO_REUSEADDR lets a second socket silently double-bind a port
    # that's already listening (two app instances would fight for the phones);
    # without it a taken port raises EADDRINUSE and we move up one - correct.
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        # phones drop connections constantly (page closed mid-poll, screen
        # off) - that's routine, not a traceback on stderr
        log.debug("phoneview client %s dropped", client_address, exc_info=True)


class _Session:
    __slots__ = ("token", "ip", "label", "kind", "ctype", "fetch",
                 "created", "fetch_lock", "frame",
                 "frame_ts", "frame_mono", "fetch_err", "pulls", "last_pull", "phones")

    def __init__(self, ip: str, label: str, kind: str = "camera",
                 ctype: str = "image/jpeg", fetch=None):
        self.token = secrets.token_urlsafe(6)
        self.ip = ip                # camera sessions; "" for screen/window sessions
        self.label = label
        self.kind = kind            # "camera" | "window"
        self.ctype = ctype
        self.fetch = fetch          # zero-arg frame source; None = camera default (by ip)
        self.created = time.time()
        self.fetch_lock = threading.Lock()
        self.frame: bytes | None = None
        self.frame_ts = 0.0         # wall clock, for human-readable ages
        self.frame_mono = 0.0       # monotonic, for the freshness gate
        self.fetch_err: str | None = None
        self.pulls = 0
        self.last_pull = 0.0
        self.phones: set[str] = set()


class PhoneShare:
    """The share registry + its on-demand HTTP server."""

    def __init__(self, fetch=_fetch_frame, bind: str | None = None):
        self._fetch = fetch
        self._bind = bind if bind is not None else BIND
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self.port: int | None = None

    # -- sessions ------------------------------------------------------------------

    def start_session(self, ip: str, label: str) -> dict:
        """Begin (or rejoin) sharing camera ip; ensures the server is up."""
        with self._lock:
            for s in self._sessions.values():
                if s.ip == ip:
                    self._ensure_server()
                    return {"token": s.token, "port": self.port}
            s = _Session(ip, label)
            self._sessions[s.token] = s
            self._ensure_server()
            log.info("phone view sharing %s on port %s", ip, self.port)
            return {"token": s.token, "port": self.port}

    def start_window_session(self, label: str, fetch) -> dict:
        """Begin (or rejoin) THE window share - one per app: the phone mirrors
        whatever the given window shows. fetch() is a zero-arg callable that
        returns the window's current client area as PNG bytes."""
        with self._lock:
            for s in self._sessions.values():
                if s.kind == "window":
                    s.fetch = fetch         # re-point at the freshly-targeted window
                    self._ensure_server()
                    return {"token": s.token, "port": self.port}
            s = _Session("", label, kind="window", ctype="image/png", fetch=fetch)
            self._sessions[s.token] = s
            self._ensure_server()
            log.info("phone view mirroring the %s window on port %s", label, self.port)
            return {"token": s.token, "port": self.port}

    def stop_session(self, token: str | None = None) -> int:
        """Drop one share (or all with token=None); last one out stops the
        server. Returns how many shares remain."""
        with self._lock:
            if token is None:
                self._sessions.clear()
            else:
                self._sessions.pop(token, None)
            if not self._sessions:
                self._shutdown_server()
            return len(self._sessions)

    def status(self) -> dict:
        now = time.time()
        with self._lock:
            sessions = [{
                "token": s.token, "ip": s.ip, "label": s.label, "kind": s.kind,
                "phones": len(s.phones), "pulls": s.pulls,
                "last_pull_ms": int((now - s.last_pull) * 1000) if s.last_pull else None,
                "frame_age_ms": int((now - s.frame_ts) * 1000) if s.frame_ts else None,
                "fetch_err": s.fetch_err,
            } for s in self._sessions.values()]
        return {"running": self._httpd is not None, "port": self.port,
                "sessions": sessions}

    def _session_for(self, token: str) -> _Session | None:
        with self._lock:
            s = self._sessions.get(token)
            if s and time.time() - s.created > SESSION_TTL:
                self._sessions.pop(token, None)
                if not self._sessions:
                    # a forgotten app instance goes fully quiet once its last
                    # share ages out - no eternal listener on a dev machine
                    self._shutdown_server()
                return None
            return s

    # -- server lifecycle ----------------------------------------------------------

    def _ensure_server(self):
        if self._httpd is not None:
            return
        handler = type("_PhoneHandler", (_Handler,), {"share": self})
        last_err: OSError | None = None
        for port in range(PORT_BASE, PORT_BASE + PORT_TRIES):
            try:
                self._httpd = _QuietServer((self._bind, port), handler)
                break
            except OSError as e:
                last_err = e
        if self._httpd is None:
            raise OSError(f"no free port in {PORT_BASE}..{PORT_BASE + PORT_TRIES - 1}: {last_err}")
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever,
                         name="phoneview-http", daemon=True).start()

    def _shutdown_server(self):
        """Synchronous: the listening socket is fully released on return, so a
        restart can never race a draining server for the port. shutdown() only
        stops the accept loop (bounded by its poll interval); in-flight handler
        threads finish on their own."""
        if self._httpd is None:
            return
        httpd, self._httpd, self.port = self._httpd, None, None
        httpd.shutdown()
        httpd.server_close()

    # -- frames --------------------------------------------------------------------

    def frame_for(self, s: _Session) -> tuple[bytes | None, float, str | None]:
        """Newest frame for a session: (bytes, age_seconds, error). Fresh-enough
        cache is served as-is; one thread refreshes while any others ride the
        cache, so the source never sees a pileup."""
        if s.frame is not None and time.monotonic() - s.frame_mono < MIN_FETCH_GAP:
            return s.frame, time.time() - s.frame_ts, s.fetch_err
        if s.fetch_lock.acquire(blocking=False):
            try:
                data = s.fetch() if s.fetch is not None else self._fetch(s.ip)
                s.frame = data
                s.frame_ts = time.time()
                s.frame_mono = time.monotonic()
                s.fetch_err = None
            except OSError as e:
                s.fetch_err = str(e)
            finally:
                s.fetch_lock.release()
        age = time.time() - s.frame_ts if s.frame_ts else 0.0
        return s.frame, age, s.fetch_err


# -- the phone page ----------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__LABEL__ - live</title>
<style>
  html, body { margin: 0; height: 100%; background: #0b0c0e; color: #cfd2d6;
               font: 14px/1.4 system-ui, sans-serif; }
  #wrap { position: fixed; inset: 0; display: flex; align-items: center;
          justify-content: center; }
  #live { max-width: 100%; max-height: 100%; display: none; }
  #bar { position: fixed; top: 0; left: 0; right: 0; display: flex; gap: .6rem;
         align-items: center; padding: .55rem .8rem;
         background: rgba(11,12,14,.75); transition: opacity .25s; }
  #bar.hide { opacity: 0; pointer-events: none; }
  #name { font-weight: 600; overflow: hidden; text-overflow: ellipsis;
          white-space: nowrap; }
  #st { margin-left: auto; white-space: nowrap; color: #8b939c; }
  #st.live { color: #7ec384; }
  #st.err { color: #ca4754; }
  #speed { background: none; border: 1px solid #3a3f45; color: #cfd2d6;
           border-radius: 6px; padding: .2rem .6rem; font: inherit; }
  #hint { position: fixed; bottom: 1rem; left: 0; right: 0; text-align: center;
          color: #8b939c; padding: 0 1rem; }
</style></head><body>
<div id="wrap"><img id="live" alt="live camera frame"></div>
<div id="bar"><span id="name">__LABEL__</span>
  <button id="speed">1s</button><span id="st">connecting&hellip;</span></div>
<div id="hint">waiting for the first frame&hellip;</div>
<script>
(function () {
  "use strict";
  var img = document.getElementById("live"), st = document.getElementById("st");
  var bar = document.getElementById("bar"), hint = document.getElementById("hint");
  var speedBtn = document.getElementById("speed");
  var base = location.pathname.replace(/\\/+$/, "");
  var interval = 1000, errs = 0, lastBlob = null, timer = null;

  function setStatus(text, cls) { st.textContent = text; st.className = cls || ""; }

  function schedule(ms) { clearTimeout(timer); timer = setTimeout(tick, ms); }

  function tick() {
    if (document.hidden) { schedule(400); return; }
    var t0 = Date.now();
    fetch(base + "/frame?t=" + t0, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      var age = parseInt(r.headers.get("X-Frame-Age") || "0", 10);
      return r.blob().then(function (b) {
        errs = 0;
        var url = URL.createObjectURL(b);
        img.onload = function () {
          if (lastBlob) URL.revokeObjectURL(lastBlob);
          lastBlob = url;
          img.style.display = "block";
          hint.style.display = "none";
          if (age > 10000) setStatus("stale - " + Math.round(age / 1000) + "s old", "err");
          else setStatus("live - " + (age / 1000).toFixed(1) + "s", "live");
        };
        img.onerror = function () { URL.revokeObjectURL(url); };
        img.src = url;
        schedule(Math.max(60, interval - (Date.now() - t0)));
      });
    }).catch(function () {
      errs++;
      setStatus("reconnecting…", "err");
      schedule(Math.min(interval * Math.pow(2, errs), 15000));
    });
  }

  speedBtn.addEventListener("click", function () {
    interval = interval === 1000 ? 500 : 1000;
    speedBtn.textContent = interval === 1000 ? "1s" : "0.5s";
  });
  document.getElementById("wrap").addEventListener("click", function () {
    bar.classList.toggle("hide");
  });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) { clearTimeout(timer); tick(); }
  });
  if (navigator.wakeLock && navigator.wakeLock.request) {
    navigator.wakeLock.request("screen").catch(function () {});
  }
  tick();
})();
</script></body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    share: PhoneShare  # bound by PhoneShare._ensure_server

    server_version = "BackupViewer"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet: plant floor, not a web server
        log.debug("phoneview http: " + fmt, *args)

    def _send(self, code: int, ctype: str, body: bytes, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        path = self.path.split("?", 1)[0]
        parts = [p for p in path.split("/") if p]
        sess = self.share._session_for(parts[1]) if len(parts) >= 2 and parts[0] == "v" else None
        if sess is None or len(parts) > 3 or (len(parts) == 3 and parts[2] != "frame"):
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return
        if len(parts) == 2:
            page = _PAGE.replace("__LABEL__", html.escape(sess.label or sess.ip))
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
            return
        sess.pulls += 1
        sess.last_pull = time.time()
        sess.phones.add(self.client_address[0])
        frame, age, err = self.share.frame_for(sess)
        if frame is None:
            what = "the camera" if sess.kind == "camera" else "the Matrox window"
            msg = f"no frame from {what} yet ({err or 'no fetch attempted'})"
            self._send(503, "text/plain; charset=utf-8", msg.encode("utf-8"),
                       {"Retry-After": "1"})
            return
        self._send(200, sess.ctype, frame, {"X-Frame-Age": str(int(age * 1000))})


# -- which of the laptop's addresses should the phone dial? ------------------------

def rank_ip(ip: str, camera_facing: str | None) -> tuple[int, str]:
    """Sort key: Windows mobile-hotspot net first (the recipe that always
    works), then other private LANs, with the camera-facing adapter last -
    that one is the robot network, the least likely place a phone lives."""
    kind = "lan"
    if ip.startswith("192.168.137."):
        order, kind = 0, "hotspot"
    elif ip == camera_facing:
        order, kind = 3, "camera network"
    elif ip.startswith(("10.", "192.168.")) or _in_172_private(ip):
        order = 1
    else:
        order = 2
    return order, kind


def _in_172_private(ip: str) -> bool:
    parts = ip.split(".")
    return len(parts) == 4 and parts[0] == "172" and parts[1].isdigit() \
        and 16 <= int(parts[1]) <= 31


def lan_urls(camera_ip: str | None, port: int, token: str) -> list[dict]:
    """Every address this machine answers on, as ready-to-dial share URLs,
    most-phone-reachable first: [{ip, url, kind}]. camera_ip=None (screen
    shares) skips the which-adapter-faces-the-camera demotion."""
    addrs: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    facing = None
    if camera_ip:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect((camera_ip, 9))   # routing lookup only; nothing is sent
                facing = s.getsockname()[0]
                addrs.add(facing)
            finally:
                s.close()
        except OSError:
            pass
    addrs.discard("127.0.0.1")
    ranked = sorted(addrs, key=lambda a: (rank_ip(a, facing)[0], a))
    return [{"ip": a, "url": f"http://{a}:{port}/v/{token}",
             "kind": rank_ip(a, facing)[1]} for a in ranked]
