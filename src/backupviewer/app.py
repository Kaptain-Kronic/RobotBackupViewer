"""Window boot. resource_path() is the single dev/frozen asset resolver:
PyInstaller re-roots this module's __file__ under sys._MEIPASS, so
Path(__file__).parent works identically in both modes.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import __version__, settings
from .api import Api

log = logging.getLogger(__name__)

BG_FALLBACK = "#323437"  # pre-CSS window color; avoids white flash


def resource_path(rel: str) -> Path:
    return Path(__file__).resolve().parent / rel


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="backupviewer", description="FANUC robot backup viewer")
    parser.add_argument("--backup", help="backup folder to open at startup")
    parser.add_argument("--debug", action="store_true", help="enable devtools")
    args = parser.parse_args(argv)

    settings.setup_logging()
    log.info("backupviewer %s starting", __version__)

    try:
        import webview
    except ImportError:
        print("pywebview is not installed. Run: pip install pywebview")
        return 1

    api = Api()
    window = webview.create_window(
        f"FANUC Backup Viewer",
        url=str(resource_path("web/index.html")),
        js_api=api,
        width=1280,
        height=860,
        min_size=(900, 600),
        background_color=BG_FALLBACK,
    )
    api.bind(window, initial_backup=args.backup)

    try:
        webview.start(gui="edgechromium", debug=args.debug)
    except Exception as e:
        log.exception("webview failed to start")
        _webview2_help(e)
        return 1
    return 0


def _webview2_help(err: Exception) -> None:
    msg = (
        "The app could not start its web view.\n\n"
        "This usually means the Microsoft Edge WebView2 Runtime is missing.\n"
        "Install it from:\n"
        "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
        f"Details: {err}"
    )
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, msg, "FANUC Backup Viewer", 0x10)
    except Exception:
        print(msg)
