# backupviewer

A fast, light weight tool for taking, organizing, and viewing FANUC robot backups.

![status](https://img.shields.io/badge/status-v1.0-e2b714)
![license](https://img.shields.io/badge/license-GPLv3-7ec384)

## The library

On launch you land on your **library** — every robot you keep, grouped into collapsible
**PLANT / LINE / ROBOT** folders. Filter by name / model / IP / note, sort by name · IP ·
last-backup date. Click a robot to open its latest backup; each row shows its IP, last
backup, saved-backup count, and honest status pills (`missing`, `no backup`, `partial`).

The folder tree **is** the library — files are law. A robot exists because its folder
exists, so you grow the library three ways:

- **discover on network** — sweep a subnet for FANUC controllers (FTP), name them, add them.
- **manually** — add a robot that has no backup yet.
- **drop it in** — copy an existing backup tree into the library folder with Explorer; a
  background watcher notices and lists it. No import wizard, no duplicate-hunting.

A selection toolbar acts on whatever robots you check:
**backup · hide · scan · manage backups**.

## Take a backup

**take a backup** connects to a controller over FTP and pulls an **"all of above"** backup
(the `MD:` device) into a per-line `Latest` mirror **and** a dated history snapshot.
Image/TFTP backups are intentionally out of scope.

Backups are written to survive interruption. Each snapshot gets a completion marker written
**last**, so a pull that dies mid-download is recognized as **partial** on the next scan and
is never mistaken for a fresh backup. A durable **run log** records every robot's
outcome, and **retry failed** re-fires exactly the robots that didn't make it.

## Fleet health scan

Select any set of robots, hit **scan**, and the app reads each one's saved backup with the
same parsers the tabs use, then reports across the whole selection. Every check is
conservative and **explainable** — it says *why* — and flags, never fixes:

| group | checks |
|---|---|
| **safety** | advanced DCS present · DCS signature mismatch (current ≠ latch) · CIP safety disabled |
| **mastering** | mastering incomplete · cloned mastering (identical master counts across robots) · low battery alarm (BLAL / SRVO-065) |
| **programs** | style table broken (enabled style → missing program) · unused S## programs · broken CALLs (target not in the backup) |
| **config** | software edition inventory + within-line drift · payloads never set (all schedules at the unset sentinel) |

Add any number of free-text **finds** (`DI[279]`, `R[151]`, a program name…) as chips —
each becomes its own report section. The report opens tight (flags first), copies to the
clipboard as text, and a click on any finding opens that robot on the tab that shows it.
Nothing touches the network — the scan reads backups already on disk.

## Compare across time

Every robot remembers its dated history. On the overview, the 🕓 date picker switches to any
past backup, and a **vs** pill compares the open backup against that date — answering the
question every troubleshoot starts with: *it ran Friday, broke Monday, what changed?* The
compare view lays two backups side by side per category (identity, frames, payloads, IO,
registers, DCS, mastering, programs) with real program diffs, and either side can come from
the library or a folder.

## Manage backups

One panel for backup hygiene and library tidy-up:

- **last run** — the durable outcome summary, with **retry failed** and copy-to-text.
- **partial backups** — snapshots whose pull died mid-download (kept, never latest, never
  auto-deleted).
- **stale backups** — robots with no completed backup in the last *N* days (adjustable).
- **selected robots** — **fix names** (from backup contents, moves the folder too),
  **merge** duplicates, **move to** another plant/line — folders always move with the entry.

## What the viewer shows

| tab | data | source files |
|---|---|---|
| **overview** | robot identity, software edition & options, master counts, memory pools, ethernet, GM wizard Q&A, motors, tasks — plus a collapsed "at backup time" section (safety, position, alarm history) and the dated-history picker | `SUMMARY.DG`, `SYSMAST.VA`, `GMWIZLOG.DT`, `ERR*.LS` |
| **frames** | tool / user / jog frames as vertical pendant-style cards, with payload schedules | `SYSFRAME.VA`, `FRAMEVAR.VA`, `SYMOTN.VA` |
| **io** | pendant categories (digital/group/uop/sop/robot/flags), IN and OUT side by side, state at backup time, rack·slot·port | `IOCONFIG.DG`, `IOSTATE.DG`, `SUMMARY.DG` |
| **registers** | R / PR / SR with comments, split into side-by-side columns on wide screens | `NUMREG.VA`, `POSREG.VA`, `STRREG.VA` |
| **programs** | every program (incl. binary-only), ★ = callable from the PLC style table, syntax-highlighted source, calls / called-by panel + expandable call tree; macros sub-view | `*.LS`, `CELLIO.VA` |
| **dcs** | safety: verify report, change history, signatures, code-styled safe-I/O logic | `DCSVRFY.DG`, `DCSCHGD*.DG` |
| **mh valves** | GM gripper / valve configuration (and magnet EOATs) | `MHGRIPDT.VA`, `MAG*.PC` |
| **system vars** | the full `SYSTEM.VA` tree; KAREL `.PC` program variables | `SYSTEM.VA`, `*.VA`/`*.VR` |
| **files** | raw browser for every file; text viewer + hex preview for binaries | everything |
| **compare** | two backups side by side, per-category, with program diffs | — |

**Backup-wide search** (`ctrl+k`) covers signals / registers structurally or free text across program
lines, IO comments, registers, frames, macros and file names. Tabs light up based on what's
actually in the backup.

## Run

Requires Python 3.10+ on Windows (uses the built-in Edge WebView2 runtime).

```powershell
pip install pywebview
python run.py                          # opens the library
python run.py --backup PATH\TO\BACKUP  # open a backup directly
python run.py --debug                  # F12 devtools
```

## Keyboard

| key | action |
|---|---|
| `1`–`9` | switch tab (numbered as shown) |
| `ctrl+k` | search the whole backup |
| `/` | focus tab filter |
| `esc` | clear filter · back to list · close |
| `backspace` | back (previous program / view) |
| `j` `k` / `↓` `↑` | move selection |
| `h` `l` / `←` `→` | switch pane in split views |
| `enter` | open selection · search signal |
| `t` / `shift+t` | theme picker / cycle theme |
| `?` | shortcut help |


## Themes

MonkeyType-style: a theme is ~9 colors. **28 built-ins ship** across four packs —
**MonkeyType** (serika dark, dracula, nord, gruvbox, matrix, rosé pine …), **Sports**,
**Cyberpunk 2077**, and **Vibes** — in a category picker. Build your own in the app with the
**custom theme editor** (live preview, saved under a Custom pack), or drop a JSON theme into
your user themes folder and it appears in the picker; either way the file is trivially
shareable:

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
  library.py      the saved-robot registry (library.json) + folder-tree scan
  ftpbackup.py    the FTP backup engine (MD: "all of above", gentle/throttled)
  healthscan.py   the fleet health-scan engine (check registry + worker job)
  backuplog.py    the durable backup-run log (survives the post-backup refresh)
  parsers/        pure text -> dict parsers (one per file family)
  web/            vanilla JS frontend, no build step (classic scripts, BV namespace)
```

```powershell
pip install pytest
python -m pytest tests -q
```

The included tests (`test_ftpbackup.py`, `test_library.py`, `test_healthscan.py`,
`test_backuplog.py`, `test_discover.py`) are self-contained — the FTP engine and health
scan run end-to-end against in-memory fakes and synthetic fixtures. The broader parser/UI
regression suite runs against a local FANUC backup fixture (real plant data, not
distributed); those tests skip gracefully when it's absent.

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
