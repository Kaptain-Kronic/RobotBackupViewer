# PyInstaller spec - build with:  pyinstaller packaging/backupviewer.spec
# (run from the repo root; use a python.org Python, not the Microsoft Store one)
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH injected by PyInstaller

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "src" / "backupviewer" / "web"), "backupviewer/web"),
        # the captured CV-X remote-desktop handshake blobs, replayed at connect
        # time by cvx_remote.py (loaded via __file__-relative path in both dev
        # and frozen builds).
        (str(ROOT / "src" / "backupviewer" / "cvx_handshake"), "backupviewer/cvx_handshake"),
    ],
    hiddenimports=[
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
    ],
    hookspath=[],
    runtime_hooks=[],
    # paramiko was briefly installed while confirming the Matrox transport, and
    # PyInstaller's static analysis drags it (+ its cryptography/nacl/bcrypt stack,
    # ~4 MB) into the graph via an optional import - the app never uses it (Matrox
    # is SMB via native ctypes), so exclude it to keep the exe lean.
    excludes=["tkinter", "test", "unittest",
              "paramiko", "cryptography", "nacl", "bcrypt"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BackupViewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=None,
)
