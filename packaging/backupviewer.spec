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
    ],
    hiddenimports=[
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest"],
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
