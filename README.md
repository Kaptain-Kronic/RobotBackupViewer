# backupviewer

A fast, minimal **ecosystem for FANUC robot backups**. Browse a backup like a mini
pendant — frames, IO, registers, programs, alarms, DCS — in a clean, keyboard-driven,
themeable UI. Keep a library of your robots, and pull fresh backups straight off a
controller over FTP.

**Smart cameras too** — the library holds cameras alongside robots, each over whatever
the camera actually speaks:

- **Matrox (MTX)**: back up its Design Assistant `da/` folder + the newest day of saved
  images over **SMB** (the `\\<ip>\mtxuser` share, `MTXuser`/`MATROX`), and browse it with
  a **photos** view showing the most recent inspection image, its pass/fail result and the
  metadata parsed from the camera's own sidecar. **Remote** embeds the camera's own web UI
  in-app with tabs — the portal home **and its Design Assistant operator page(s)**, auto-
  discovered — fullscreen-capable, with a separate-window fallback if a page refuses framing.
- **Keyence (CV-X)**: back up its `cv-x/setting/` config tree over **anonymous FTP**, and
  **remote into its live screen** — a fullscreen-capable mirror of the controller's 1024×768
  display with mouse control, speaking the CV-X's own remote-desktop protocol (no Keyence
  software or Terminal PC needed).

Network discovery sweeps both FTP (robots + CV-X) and SMB (Matrox) and files everything
under the right device type automatically; a robot/camera filter keeps a busy cell legible.

![status](https://img.shields.io/badge/status-v0.99q-e2b714)
![license](https://img.shields.io/badge/license-GPLv3-7ec384)

## The shell

On launch you land on a **home menu**:

- **open backup** — browse any backup folder on disk.
- **add to library** — save a backup to a local library, organized **PLANT / LINE / ROBOT**
  (add manually or import an existing folder; IP/model/F-number auto-fill from the backup).
- **take a new backup** — connect to a controller over FTP and pull an **"all of above"**
  backup (the `MD:` device — the same ASCII set the viewer reads) into a `Latest` mirror +
  dated history, with a pre-flight reachability **probe** and live progress.
  Image/TFTP backups are intentionally out of scope.

## What the viewer shows

| tab | data | source files |
|---|---|---|
| **overview** | robot identity, software & options, master counts, memory pools, ethernet, GM wizard Q&A, motors, tasks — plus a collapsed "at backup time" section (safety, position, alarm history) | `SUMMARY.DG`, `SYSMAST.VA`, `GMWIZLOG.DT`, `ERR*.LS` |
| **frames** | tool / user / jog frames as vertical pendant-style cards, with payload schedules | `SYSFRAME.VA`, `FRAMEVAR.VA`, `SYMOTN.VA` |
| **io** | pendant categories (digital/group/uop/sop/robot/flags), IN and OUT side by side, state at backup time, rack·slot·port | `IOCONFIG.DG`, `IOSTATE.DG`, `SUMMARY.DG` |
| **registers** | R / PR / SR with comments, split into side-by-side columns on wide screens | `NUMREG.VA`, `POSREG.VA`, `STRREG.VA` |
| **programs** | every program (incl. binary-only), ★ = callable from the PLC style table, syntax-highlighted source, calls / called-by panel + expandable call tree; macros sub-view | `*.LS`, `CELLIO.VA` |
| **dcs** | safety: verify report, change history, signatures, code-styled safe-I/O logic | `DCSVRFY.DG`, `DCSCHGD*.DG` |
| **mh valves** | GM gripper / valve configuration | `MHGRIPDT.VA` |
| **system vars** | the full `SYSTEM.VA` tree; KAREL `.PC` program variables | `SYSTEM.VA`, `*.VA`/`*.VR` |
| **photos** *(camera)* | the most recent Matrox inspection image + pass/fail, recipe, exposure, camera identity and per-tool results, over a pass/fail-filterable thumbnail grid | `SavedImages/*.jpg` `.png` `.txt` |
| **files** | raw browser for every file; text viewer + hex preview for binaries | everything |
| **compare** | two backups side by side, per-category, with program diffs | — |

Signal names are pendant-style everywhere (`DI[279]`, never `DIN[279]`). **Backup-wide
search** (`ctrl+k`) covers signals/registers structurally or free text across program lines,
IO comments, registers, frames, macros and file names. Tabs light up based on what's
actually in the backup.

## Run

Requires Python 3.10+ on Windows (uses the built-in Edge WebView2 runtime).

```powershell
pip install pywebview
python run.py                          # opens the home menu
python run.py --backup PATH\TO\BACKUP  # open a backup directly
python run.py --debug                  # F12 devtools
```

## Keyboard

| key | action |
|---|---|
| `1`–`9` | switch tab |
| `ctrl+k` | search the whole backup |
| `/` | focus tab filter |
| `esc` | clear filter · back · close |
| `j` `k` / `↓` `↑` | move selection |
| `h` `l` / `←` `→` | switch pane in split views |
| `enter` | open selection · search signal |
| `ctrl+o` | open backup folder |
| `t` / `shift+t` | theme picker / cycle theme |
| `?` | shortcut help |

Font size and UI scale live behind the `aa` button (persisted). The logo is the home button.

## Themes

MonkeyType-style: a theme is ~9 colors. A dozen built-ins ship (serika dark, dracula, nord,
gruvbox, matrix, …). Drop your own JSON into `%APPDATA%\BackupViewer\themes\` and it appears
in the picker:

```json
{
  "id": "mytheme",
  "name": "my theme",
  "colors": {
    "bg": "#323437", "bg2": "#2c2e31", "sub": "#646669", "subAlt": "#51545a",
    "text": "#d1d0c5", "accent": "#e2b714", "error": "#ca4754",
    "ok": "#7ec384", "warn": "#e2b714"
  }
}
```

## Development

```
src/backupviewer/
  app.py          window boot (resource_path works in dev + PyInstaller)
  api.py          the entire JS<->Python bridge ({ok,data}/{ok,error} envelopes)
  session.py      backup folder scan, case-insensitive index, lazy parse cache
  library.py      the saved-robot registry (library.json)
  ftpbackup.py    the FTP backup engine (MD: "all of above", gentle/throttled)
  mtxbackup.py    the Matrox camera SMB backup (da/ + newest SavedImages, per-camera)
  keyencebackup.py the Keyence CV-X camera FTP backup (cv-x/setting, per-camera)
  cvx_remote.py   the Keyence CV-X live remote desktop (screen mirror + mouse, MJPEG bridge)
  cvx_handshake/  captured CV-X remote-desktop handshake blobs, replayed at connect time
  parsers/        pure text -> dict parsers (one per file family)
  web/            vanilla JS frontend, no build step (classic scripts, BV namespace)
```

```powershell
pip install pytest
python -m pytest tests -q
```

The included tests (`test_ftpbackup.py`, `test_library.py`) are self-contained — the FTP engine
runs end-to-end against an in-memory fake controller. The broader parser/UI regression
suite runs against a local FANUC backup fixture (real plant data, not distributed); those
tests skip gracefully when it's absent.

## Packaging (share a single .exe)

```powershell
pip install pyinstaller
python -m PyInstaller packaging/backupviewer.spec --noconfirm
```

Produces `dist/BackupViewer.exe` (onefile, no console). Target machines need the WebView2
runtime (preinstalled on Windows 10/11; the app shows a download link if missing).

## License

GPLv3 — free and open. Use it, change it, share it; derivatives stay open. See `LICENSE`.

## File format notes

Quirks handled by the parsers:

- Mixed line endings inside one backup (`SUMMARY.DG` is LF, most others CRLF).
- `SUMMARY.DG` is pseudo-HTML; sections split on `<H2><A NAME="n">` headers.
- `.LS` position values may be masked as `********`.
- `IOSTATE.DG` state columns can run flush against the index bracket (`GOUT[  93]20752`).
- Frame/tool names live in `FRAMEVAR.VA` `SETUP_DATA[group, type, index].$COMMENT`
  with type 1=tool, 2=jog, 3=uframe.
- Report-style `.LS` files (`ERRALL.LS`, `LOGBOOK.LS`) are distinguished from real TP
  programs by content (`/PROG` header), not name.
