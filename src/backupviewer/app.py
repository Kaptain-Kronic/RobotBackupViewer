"""Window boot. resource_path() is the single dev/frozen asset resolver:
PyInstaller re-roots this module's __file__ under sys._MEIPASS, so
Path(__file__).parent works identically in both modes.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, settings
from .api import Api

log = logging.getLogger(__name__)

BG_FALLBACK = "#323437"  # pre-CSS window color; avoids white flash


def resource_path(rel: str) -> Path:
    return Path(__file__).resolve().parent / rel


# ---- WebView2 boot resilience ---------------------------------------------------
# Field failure (Diag/ capture, 2026-07-02): CreateCoreWebView2ControllerAsync
# dies with 0x8007139F on some machines (Microsoft tracks it without a single
# root cause; an ancient integrated-GPU driver is the usual suspect, corrupt
# profiles/AV the runners-up). pywebview SWALLOWS the failure - it logs one
# ERROR and leaves a dead window, so webview.start() never raises and the user
# just sees the app "not boot". The rescue: watch pywebview's logger for that
# record, close the dead window, and relaunch ourselves ONCE in fallback mode
# (software rendering + a stable profile folder). A fallback boot that works is
# remembered, so later launches skip the dead first attempt.

_WV2_RESCUE_ENV = "BV_WEBVIEW2_RESCUE"          # set on the relaunched process
_WV2_FAIL_MARK = "WebView2 initialization failed"
_WV2_SETTING = "webview2_fallback"


class _WebView2FailureWatch(logging.Handler):
    """Fires when pywebview logs its (otherwise-swallowed) WebView2 init
    failure; closes the dead window so webview.start() returns to main()."""

    def __init__(self, window):
        super().__init__(level=logging.ERROR)
        self.window = window
        self.failed = False

    def emit(self, record):
        try:
            if _WV2_FAIL_MARK not in record.getMessage():
                return
        except Exception:  # noqa: BLE001 - a logging handler must never raise
            return
        self.failed = True
        try:
            self.window.destroy()
        except Exception:  # noqa: BLE001 - window may already be gone
            pass


def _fallback_storage_dir() -> Path:
    """A stable, per-user, machine-local WebView2 profile. Replaces pywebview's
    default (a just-deleted temp path the browser recreates) - temp dirs are
    exactly where AV interference and ACL oddities live."""
    base = os.environ.get("LOCALAPPDATA") or str(settings.app_dir())
    return Path(base) / "BackupViewer" / "webview2"


def _apply_fallback_env() -> dict:
    """Arm the fallback: software rendering via the WebView2 loader's
    environment hook (broken GPU drivers are the top 0x8007139F suspect) and a
    fresh stable profile (corrupt profiles are the runner-up). Returns the
    extra kwargs for webview.start()."""
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        "--disable-gpu --disable-gpu-compositing"
    )
    d = _fallback_storage_dir()
    shutil.rmtree(d, ignore_errors=True)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("could not create %s - letting WebView2 create it", d)
    return {"storage_path": str(d), "private_mode": False}


def _relaunch_cmd() -> list[str]:
    """This process's own launch command (frozen exe vs `python run.py`)."""
    if getattr(sys, "frozen", False):
        return [sys.executable] + sys.argv[1:]
    return [sys.executable] + sys.argv


def _next_boot_action(failed: bool, mode: str, rescued: bool) -> str:
    """After webview.start() returns: 'ok', 'give-up' (a rescue already
    failed - never chain relaunches), or which mode to relaunch into."""
    if not failed:
        return "ok"
    if rescued:
        return "give-up"
    return "relaunch-normal" if mode == "fallback" else "relaunch-fallback"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="backupviewer", description="robot backup viewer")
    parser.add_argument("--backup", help="backup folder to open at startup")
    parser.add_argument("--debug", action="store_true", help="enable devtools")
    parser.add_argument("--diagnose", metavar="HOST",
                        help="read-only FTP probe of a controller for auto-naming "
                             "debug: writes JSON to app.log and exits (no window)")
    parser.add_argument("--webview2-normal", action="store_true",
                        help="forget a remembered WebView2 fallback mode and "
                             "start with normal (GPU) rendering")
    args = parser.parse_args(argv)

    settings.setup_logging()
    log.info("backupviewer %s starting", __version__)

    if args.diagnose:
        return _run_diagnose(args.diagnose)

    try:
        import webview
    except ImportError:
        print("pywebview is not installed. Run: pip install pywebview")
        return 1

    # which rendering mode this boot runs in: a rescue relaunch dictates it;
    # otherwise a remembered fallback (unless the user asked to forget it)
    rescued = os.environ.pop(_WV2_RESCUE_ENV, "")
    if args.webview2_normal:
        settings.set_value(_WV2_SETTING, None)
    if rescued:
        fallback = rescued == "fallback"
    else:
        fallback = bool(settings.get(_WV2_SETTING)) and not args.webview2_normal

    start_kwargs = {"gui": "edgechromium", "debug": args.debug}
    if fallback:
        log.info("WebView2 fallback mode: software rendering + stable profile")
        start_kwargs.update(_apply_fallback_env())

    api = Api()
    window = webview.create_window(
        "Backup Viewer",
        url=str(resource_path("web/index.html")),
        js_api=api,
        width=1280,
        height=860,
        min_size=(900, 600),
        background_color=BG_FALLBACK,
    )
    api.bind(window, initial_backup=args.backup)

    watch = _WebView2FailureWatch(window)
    logging.getLogger("pywebview").addHandler(watch)
    try:
        webview.start(**start_kwargs)
    except Exception as e:
        log.exception("webview failed to start")
        _webview2_help(e, runtime_missing=True)
        return 1
    finally:
        logging.getLogger("pywebview").removeHandler(watch)

    action = _next_boot_action(watch.failed, "fallback" if fallback else "normal", bool(rescued))
    if action == "ok":
        # remember what works so the next launch goes straight there
        if fallback and not settings.get(_WV2_SETTING):
            settings.set_value(_WV2_SETTING, True)
            log.info("fallback boot succeeded - remembered for future launches")
        elif not fallback and settings.get(_WV2_SETTING):
            settings.set_value(_WV2_SETTING, None)
        return 0
    if action == "give-up":
        log.error("WebView2 failed in both normal and fallback modes")
        _webview2_help(RuntimeError("WebView2 could not start in any mode (see app.log)"))
        return 1
    target = "fallback" if action == "relaunch-fallback" else "normal"
    log.warning("WebView2 init failed - relaunching once in %s mode", target)
    if fallback:
        settings.set_value(_WV2_SETTING, None)      # the remembered mode was wrong
    env = dict(os.environ)
    env[_WV2_RESCUE_ENV] = target
    try:
        subprocess.Popen(_relaunch_cmd(), env=env, close_fds=True)
    except OSError:
        log.exception("could not relaunch for the WebView2 rescue")
        _webview2_help(RuntimeError("WebView2 failed and the automatic rescue "
                                    "could not start (see app.log)"))
    return 1


def _run_diagnose(host: str) -> int:
    """`--diagnose HOST`: probe a controller's FTP for auto-naming debug. Prints
    JSON (dev/console run) and pops a MessageBox (windowed exe has no console);
    discover.diagnose_controller already logged the full probe to app.log."""
    from . import discover

    result = discover.diagnose_controller(host)
    print(json.dumps(result, indent=2))
    resolved = result.get("resolved") or {}
    msg = (
        f"Controller {host}\n\n"
        f"Resolved name: {resolved.get('name') or '(none - falls back to IP)'}\n"
        f"Model: {resolved.get('model') or '-'}\n"
        f"F-number: {resolved.get('f_number') or '-'}\n"
        f"Banner: {result.get('banner') or '-'}\n"
        f"Error: {result.get('error') or '(none)'}\n\n"
        f"Full probe written to:\n{settings.app_dir() / 'app.log'}"
    )
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, msg, "BackupViewer - controller diagnose", 0x40)
    except Exception:  # noqa: BLE001 - console run / non-Windows
        pass
    return 0


def _webview2_help(err: Exception, runtime_missing: bool = False) -> None:
    if runtime_missing:
        msg = (
            "The app could not start its web view.\n\n"
            "This usually means the Microsoft Edge WebView2 Runtime is missing.\n"
            "Install it from:\n"
            "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
            f"Details: {err}"
        )
    else:
        msg = (
            "The app's web view failed to start, even with software rendering.\n\n"
            "Things that fix this, in order of likelihood:\n"
            "1. Update the graphics driver (old integrated-GPU drivers are the\n"
            "    most common cause)\n"
            "2. Repair 'Microsoft Edge WebView2 Runtime'\n"
            "    (Settings > Apps > Installed apps > ... > Modify)\n"
            "3. Have IT check antivirus exclusions for msedgewebview2.exe\n\n"
            f"Full log: {settings.app_dir() / 'app.log'}\n\n"
            f"Details: {err}"
        )
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, msg, "Backup Viewer", 0x10)
    except Exception:
        print(msg)
