# INVENTORY — file-level map of this repo

*Documentation pass, phase 1. Breadth, not depth: every file below was opened and
skimmed for purpose. Nothing was edited, renamed or fixed to produce this map —
problems found are noted, not acted on.*

Generated 2026-07-28 against `main` @ `e594785` **plus the uncommitted working tree**
(11 modified files and the then-untracked `components/icons.js`).

> **⚠️ This is a dated snapshot, not a live document.** `~lines` and every file
> description are as of generation. A repair pass ran straight afterwards
> (`b47b4bc`..`da10c92`, 2026-07-28) and acted on several findings below; those are
> marked **✅ RESOLVED** inline with the commit that closed them. The original
> wording is kept deliberately — the record of what was found is the point — but a
> resolved item is history, not a to-do. Everything unmarked still stands.
>
> When per-subsystem docs land, they supersede this file for their own area. This
> map's remaining job is breadth: what exists, and where.

**Scope.** 243 files / ~65,429 lines. Covers everything in the working tree except: the
`.git` internals, build outputs (`dist/`, `build/`, `__pycache__/`), the private
`SampleBackup/` fixture tree, the `.rmd` model corpus (61 binary robot-model blobs
that are input data, not source), and the two local-only real-plant reference files
(`robots.json`, `robots.applied.txt`) which are deliberately never read or described here.

**Reading the columns.** `~lines` is newline count (binaries show size instead; the 28
theme JSONs are one collapsed row). `status` is `active` unless a reader grepped and
found nothing referencing the file (`possibly-dead`), it is a build/tool product
(`generated`), it is third-party (`vendored`), or it could not be confidently placed
(`unclear` — reason given inline).

---

## The map

| path | ~lines | what it is | subsystem | status |
|---|---|---|---|---|
| `.gitignore` | 21 | ignore rules for pycache/build/dist plus the local-only sample backup, real ip list and diag folders | build/config | active |
| `build_exe.log` | 106 | pyinstaller transcript of one successful onefile build on store python 3.13 with pyinstaller 6.20 *(untracked, local-only)* | build/config | generated |
| `CHANGELOG.md` | 935 | prose changelog newest-first: a large unreleased section over v1.4 back to v0.1, feature by feature | docs | active |
| `CLAUDE.md` | 220 | the build contract: locked stack, layer map, composition/honesty rules and the plant-identifier firewall | docs | active |
| `CVX_FTP_LAYOUT.md` | 40 | field notes on the cv-x ftp tree and simulator workspace layout, and what env.dat cannot prove | docs | active |
| `edit_sandbox.html` | 320 | untracked browser rig driving the real lseditor component against the real css, asserting caret/enter/undo — untracked, excluded via .git/info/exclude; grepped the repo for "edit_sandbox" and nothing references it. its own header says it "dies when the component is trusted", and components/lseditor.js has shipped | program editor | possibly-dead |
| `edit_workspace_sandbox.html` | 693 | untracked ui mock of the edit workspace (rail tabs, panes, find/replace) on fake robots, nothing talks to python — untracked, git-excluded; grepped for "edit_workspace_sandbox" with no hits. superseded by the shipped workspace.js + tabs/edit.js | program editor | possibly-dead |
| `LICENSE` | 620 | verbatim gnu gpl v3 license text, the license the readme points at | docs | vendored |
| `pyproject.toml` | 28 | project metadata (v1.4, pywebview), the pytest pythonpath=src a fresh clone needs to collect tests, and the `probe` marker + `addopts` that keep the default run fast | build/config | active |
| `README.md` | 240 | public readme: feature tour, tab-to-source-file table, run/package/test commands, theme json shape | docs | active |
| `ROADMAP.md` | 264 | lane-claiming roadmap: shipped/building/decided/open items per subsystem, plus the 2.0 editing principles | docs | active |
| `run.py` | 10 | dev launcher and pyinstaller entry script: puts src on sys.path, calls backupviewer.app.main | build/config | active |
| `run_libraryimporter.py` | 10 | dev launcher and pyinstaller entry script for the companion libraryimporter app | LibraryImporter | active |
| `split_diff_sandbox.html` | 878 | untracked ui mock of the 4-pane split tree and inline pane diff, with a mock aligner and review modal — untracked, git-excluded; grepped for "split_diff_sandbox" with no hits. its header says the js aligner is mock-only and the real one is compare.align_program_lines; the split tree, inline diff and pdiffview have all shipped | program editor | possibly-dead |
| `docs/INVENTORY.md` | 610 | this file: dated file-level map of the repo plus the findings from the phase-1 documentation pass, with resolved items marked inline | docs | active |
| `docs/proposals/home-split.md` | 277 | investigation of tabs/home.js (2,277 lines) by responsibility, where the real seams are, what must become shared components first, and a phased sequence — no code changed | docs | active |
| `packaging/backupviewer.ico` | *75 KB* | multi-resolution app icon embedded in the exe and inherited by the pywebview window | build/config | active |
| `packaging/backupviewer.spec` | 58 | pyinstaller onefile spec: bundles web/ and cvx_handshake/, edgechromium hidden imports, excludes paramiko | build/config | active |
| `packaging/libraryimporter.spec` | 41 | pyinstaller onefile spec for libraryimporter.exe, bundling src/libraryimporter/web, no icon | build/config | active |
| `src/backupviewer/__init__.py` | 1 | package marker holding the single source of the app version string (1.4) | shared/infra | active |
| `src/backupviewer/__main__.py` | 6 | python -m backupviewer entry point delegating to app.main | shared/infra | active |
| `src/backupviewer/api.py` | 3512 | the pywebview bridge class: 117 @_endpoint methods returning {ok,data} envelopes across every feature area | shared/infra | active |
| `src/backupviewer/app.py` | 251 | window boot: arg parsing, resource_path, pywebview window, and the one-shot webview2 failure rescue relaunch | shared/infra | active |
| `src/backupviewer/backuplog.py` | 144 | durable backup-run log in %appdata%: per-run job rows, retry attempt counting, failed-spec list, never a password | backup capture | active |
| `src/backupviewer/compare.py` | 527 | pure two-backup diff functions: io/registers/frames/payloads/programs rows plus tp-line alignment | compare engine | active |
| `src/backupviewer/cvx_handshake/chan8502_tx.bin` | *96 KB* | captured cv-x 8502 control-channel client handshake: 13 messages incl. six 16 kb blobs, replayed at connect | remote/mobile | active |
| `src/backupviewer/cvx_handshake/chan8503_tx.bin` | *0.2 KB* | captured cv-x 8503 aux-channel handshake: a single channel-open message replayed to open the aux socket | remote/mobile | active |
| `src/backupviewer/cvx_handshake/chan8504_tx.bin` | *0.2 KB* | captured cv-x 8504 video-channel handshake: channel-open, video-service open and the frame-ack prime message | remote/mobile | active |
| `src/backupviewer/cvx_remote.py` | 508 | cv-x remote-desktop client: handshake replay on 3 sockets, jpeg frame harvest, mouse events, mjpeg server | remote/mobile | active |
| `src/backupviewer/discover.py` | 669 | subnet scan job finding fanuc/keyence over ftp and matrox via ethernet/ip, plus adapter list and live name probe | backup capture | active |
| `src/backupviewer/ftpbackup.py` | 627 | ftp backup engine: gentle md: pull, dated+latest tree, .part/complete-marker crash safety, shared job base | backup capture | active |
| `src/backupviewer/healthscan.py` | 1023 | fleet health-scan engine: 17-check registry, lazy per-robot parse context, threaded job, fleet-wide verdict passes | flag scanning | active |
| `src/backupviewer/keyence_workspace.py` | 374 | cv-x simulator workspace.xml writer/reader plus flat-folder export guarded by a we-created-this ledger | cameras | active |
| `src/backupviewer/keyencebackup.py` | 359 | cv-x camera backup job over anonymous ftp, plus pre-flight probe, read-only diagnose and self-naming | cameras | active |
| `src/backupviewer/kinematics_builtin.py` | 247 | built-in kinematics table: one chain (joint placements + faceplate) per fanuc robot type, some validated | 3D viewer | active |
| `src/backupviewer/library.py` | 1484 | persistent robot library in %appdata%: folder-tree-as-truth scan, robot.json sidecars, rename/merge/relocate | library | active |
| `src/backupviewer/modeldb.py` | 151 | robot-kinematics registry merging the built-in table under roboguide .def imports, with strict type matching | 3D viewer | active |
| `src/backupviewer/mtxbackup.py` | 470 | matrox camera backup job over smb: wnet+credential login, da tree and newest images copy, probe and naming | cameras | active |
| `src/backupviewer/parsers/__init__.py` | 30 | parsers package init holding TAB_REQUIREMENTS, the map of which backup files make each ui tab available | backup parsing | active |
| `src/backupviewer/parsers/alarms.py` | 75 | parses ERRALL/ERRACT/ERRHIST .LS alarm history into seq/datetime/code/severity rows plus unparsed lines | backup parsing | active |
| `src/backupviewer/parsers/callgraph.py` | 114 | extracts CALL/RUN/macro edges from .LS bodies into a call graph plus per-line clickable jump hops | backup parsing | active |
| `src/backupviewer/parsers/common.py` | 61 | shared parsing helpers: cp1252 read_text, binary sniff, scalar coercion, fanuc two-digit date/time | backup parsing | active |
| `src/backupviewer/parsers/curpos.py` | 101 | parses CURPOS.DG per-group joint/world pose and FRAME.DG tool-frame rows used to pose the 3d robot | 3D viewer | active |
| `src/backupviewer/parsers/cvx_image.py` | 303 | decodes keyence cv-x setting .bmp files: 8-bpp intensity photo or 24-bpp packed 15-bit height, to rgba | cameras | active |
| `src/backupviewer/parsers/cvx_inspect.py` | 96 | reads the inspection program name from a cv-x inspect.dat (two copies must agree) and elects a camera name | cameras | active |
| `src/backupviewer/parsers/dcs.py` | 576 | parses DCSVRFY/DCSCHGD/DCSDIFF .DG verify reports into classified sections, signatures and ok/chgd/ng counts | backup parsing | active |
| `src/backupviewer/parsers/dcszones.py` | 627 | builds the 3d zone payload from DCSPOS.VA shadow tables plus the DCSVRFY.DG report: zones, frames, user models | 3D viewer | active |
| `src/backupviewer/parsers/frames.py` | 77 | builds the tool/jog/uframe model from SYSFRAME.VA positions, FRAMEVAR.VA comments and active frame numbers | backup parsing | active |
| `src/backupviewer/parsers/gmwizlog.py` | 83 | parses the setup-wizard log GMWIZLOG.DT into header fields plus ordered q&a / event / failure entries | backup parsing | active |
| `src/backupviewer/parsers/io_dg.py` | 179 | parses IOCONFIG.DG definitions and IOSTATE.DG states and merges them into per-type tables with rack/slot/port | backup parsing | active |
| `src/backupviewer/parsers/kinematics.py` | 148 | forward kinematics over a parsed .def chain: joint frames, faceplate pose and flange-offset measurement | 3D viewer | active |
| `src/backupviewer/parsers/ls_edit.py` | 345 | byte-faithful .LS edit engine: split/emit body records, splice /ATTR and /POS values, latin-1 round-trip | program editor | active |
| `src/backupviewer/parsers/ls_program.py` | 197 | parses a .LS tp program: /PROG and /ATTR header, body lines, /POS points and lbl/jmp label cross-reference | backup parsing | active |
| `src/backupviewer/parsers/macros.py` | 44 | parses $MACROTABLE from SYSMACRO.VA into the macro list, a fallback for SUMMARY.DG's friendlier macro section | backup parsing | active |
| `src/backupviewer/parsers/magnet.py` | 68 | detects a magnet gripper from MAG*.PC karel programs and groups its R[800-899] config registers | backup parsing | active |
| `src/backupviewer/parsers/mastering.py` | 41 | parses $DMR_GRP from SYSMAST.VA into per-group master/reference encoder counts and mastered flags | backup parsing | active |
| `src/backupviewer/parsers/mhvalves.py` | 209 | rebuilds the pendant mh valve menus from MHGRIPDT.VA, resolving *_SN slots through signal tables to DI/DO | backup parsing | active |
| `src/backupviewer/parsers/mtx_portal.py` | 67 | scrapes a matrox camera portal's html for DesignAssistant operator page urls (hrefs or prj-name rows) | cameras | active |
| `src/backupviewer/parsers/mtx_saved_image.py` | 169 | parses matrox SavedImages .txt sidecars and groups jpg/png/txt photo triples into sorted grid records | cameras | active |
| `src/backupviewer/parsers/payloads.py` | 51 | builds payload schedules per motion group from $PLST_GRPn in SYMOTN.VA, flagging empty slots uninit | backup parsing | active |
| `src/backupviewer/parsers/registers.py` | 48 | parses NUMREG.VA, POSREG.VA and STRREG.VA into r / pr / sr register lists with comments | backup parsing | active |
| `src/backupviewer/parsers/roboguidedef.py` | 104 | parses a roboguide .def xml into joint chain, faceplate and zero offset, plus robot-type name normalization | 3D viewer | active |
| `src/backupviewer/parsers/styles.py` | 35 | parses the $STYLE_NAME/$STYLE_COMNT/$STYLE_ENAB plc style table (CELLIO.VA or SYSTEM.VA) into style rows | backup parsing | active |
| `src/backupviewer/parsers/summary_dg.py` | 368 | parses SUMMARY.DG pseudo-html into identity, options, motors, memory, tasks, safety, positions, ethernet, macros | backup parsing | active |
| `src/backupviewer/parsers/sysvars.py` | 176 | merges [*SYSTEM*] records across the ~two dozen carrier .VA files and expands one into a tree or flat leaf map | backup parsing | active |
| `src/backupviewer/parsers/va.py` | 232 | tokenizer for .VA variable dumps: VaRecord/VaFile plus scalar-array, position-array and struct-field readers | backup parsing | active |
| `src/backupviewer/phoneview.py` | 408 | phone live-view relay: token-gated localhost http server, single-flight cached camera/window frames, phone page | remote/mobile | active |
| `src/backupviewer/qr.py` | 264 | hand-rolled qr encoder (byte mode, ec level l, versions 1-5) returning a 0/1 module matrix | remote/mobile | active |
| `src/backupviewer/screengrab.py` | 159 | windows bitblt capture of a named window's client area, dpi-aware, encoded to png with ctypes+zlib | remote/mobile | active |
| `src/backupviewer/search.py` | 134 | backup-wide search: type[n] signal queries vs free text across programs, io, registers, frames, macros, filenames | backup parsing | active |
| `src/backupviewer/session.py` | 421 | BackupSession: recursive case-insensitive file index, lazy parse cache, ls/karel classify, backup-type sniff | backup parsing | active |
| `src/backupviewer/settings.py` | 103 | settings.json under %appdata% with atomic locked writes, plus app_dir, library_root, sim_root and logging setup | shared/infra | active |
| `src/backupviewer/updatecheck.py` | 109 | github releases/latest check with version compare and a policy that only lets the frozen exe auto-check | shared/infra | active |
| `src/backupviewer/web/css/base.css` | 527 | root css: theme variable contract (--bg/--accent/--edge/--panel), app shell layout, chrome bars, bgfx layers | theming | active |
| `src/backupviewer/web/css/components.css` | 2023 | the app's single component stylesheet: cards, tables, pills, modals, plus per-tab styles for every subsystem | theming | active |
| `src/backupviewer/web/fonts/Orbitron-VariableFont_wght.ttf` | *38 KB* | bundled orbitron variable font, loaded by base.css @font-face and offered as the 'rog' ui font in settings | theming | vendored |
| `src/backupviewer/web/index.html` | 135 | the single page: top chrome, tabbar, toolbar, #view, jobstrip, statusbar, and the ordered script list | shared/infra | active |
| `src/backupviewer/web/js/api.js` | 142 | promise wrapper over the pywebview bridge: {ok,data} envelope, solo/cvx-window sid injection, slow-call dedupe | shared/infra | active |
| `src/backupviewer/web/js/bgfx.js` | 2080 | canvas/css background-effects engine: 18 themed looks (16 canvas) with global and per-effect sliders | theming | active |
| `src/backupviewer/web/js/components/backuptabs.js` | 224 | BV.session plus the #sessionbar browser-style backup tab strip: switch, close, reorder, pop out, tear off | shared/infra | active |
| `src/backupviewer/web/js/components/builders.js` | 82 | BV.kv / BV.card / BV.hero: dom builders for key-value lists, the card shell and the identity hero line | shared/infra | active |
| `src/backupviewer/web/js/components/checklist.js` | 135 | BV.checklist: the one multiselect controller — shift-click ranges, tri-state group boxes, rebind-safe selection | shared/infra | active |
| `src/backupviewer/web/js/components/dragreorder.js` | 162 | BV.dragReorder: generic drag-to-reorder across drop zones with insertion markers, edge auto-scroll, click guard | shared/infra | active |
| `src/backupviewer/web/js/components/fk.js` | 110 | BV.fk: js twin of the FANUC forward-kinematics chain — 4x4 matrix math over an imported .def joint chain | 3D viewer | active |
| `src/backupviewer/web/js/components/framecard.js` | 43 | BV.frameCard: the tool/uframe card — title, status pills, subtitle, xyzwpr list and config line | shared/infra | active |
| `src/backupviewer/web/js/components/icons.js` | 48 | BV.icon: 4 inline stroke-svg glyphs (phone/gear/help/remote) plus a boot sweep filling [data-icon] holders | shared/infra | active |
| `src/backupviewer/web/js/components/libtree.js` | 215 | BV.libTree: plant to line to robot grouped collapsible library tree with filter, persisted folds, nested cameras | library | active |
| `src/backupviewer/web/js/components/lseditor.js` | 461 | BV.lsEditor: single-layer contenteditable TP code editor — own undo stack, caret offsets, alignment gap blocks | program editor | active |
| `src/backupviewer/web/js/components/multitable.js` | 249 | BV.MultiTable: two VTables driven as one — split halves or paired lists, lockable linked scroll, pane switching | shared/infra | active |
| `src/backupviewer/web/js/components/pdiffview.js` | 150 | BV.pdiffView: aligned side-by-side program-line diff renderer with equiv rows, stats pills and prev/next jumps | compare engine | active |
| `src/backupviewer/web/js/components/pill.js` | 29 | BV.pill plus .node/.map: the rounded status badge as an html string, a live node, or a status-to-variant map | shared/infra | active |
| `src/backupviewer/web/js/components/proj3d.js` | 122 | BV.proj3d: turntable orbit projection, xyzwpr frame transform and polygon-prism builder, pure math no dom | 3D viewer | active |
| `src/backupviewer/web/js/components/search.js` | 57 | BV.searchBox: debounced filter input with the '/' hint, match counter and escape/enter handling | shared/infra | active |
| `src/backupviewer/web/js/components/segmented.js` | 66 | BV.segmented: the .seg sub-tab pill row, controlled or uncontrolled, with counts and multi-select | shared/infra | active |
| `src/backupviewer/web/js/components/table.js` | 53 | BV.table: the small static .tbl html table for bounded row sets, kept deliberately out of keyboard nav | shared/infra | active |
| `src/backupviewer/web/js/components/vsdiff.js` | 99 | BV.vsDiff: highlight-diffs toggle plus row-tint markers and io/register/program/macro field comparators | compare engine | active |
| `src/backupviewer/web/js/components/vtable.js` | 423 | BV.VTable: the windowed table — sync data or async paging, sort, column resize/autofit, row menu, state persist | shared/infra | active |
| `src/backupviewer/web/js/cvxremote.js` | 313 | cv-x remote overlay: mjpeg screen mirror plus full mouse forwarding, session adopt/rebind for pop-outs | remote/mobile | active |
| `src/backupviewer/web/js/highlight_tp.js` | 73 | regex tokenizer that wraps FANUC TP program lines in tp-* spans for themed syntax highlighting | shared/infra | active |
| `src/backupviewer/web/js/jobs.js` | 299 | BV.jobs: 500ms backup-job poller, run-wide progress jobstrip with live details panel, global busy indicator | backup capture | active |
| `src/backupviewer/web/js/keys.js` | 154 | global keydown map (number-row tabs, ctrl+k/e, j/k, esc, backspace) plus the shortcuts help modal | shared/infra | active |
| `src/backupviewer/web/js/manage_ui.js` | 271 | the manage-backups modal: last-run log with retry-failed, partial-snapshot review, stale list, tidy actions | library | active |
| `src/backupviewer/web/js/mtxremote.js` | 168 | matrox remote overlay: sandboxed iframe tabs of the camera's own web ui, window fallback if unframeable | remote/mobile | active |
| `src/backupviewer/web/js/phoneview.js` | 212 | qr handoff modal that mirrors this window (or a camera feed) to a phone, with a firewall-fix help panel | remote/mobile | active |
| `src/backupviewer/web/js/router.js` | 492 | hash router and app boot: tabbar build, number-key badges, chrome measuring, cubes, status bar, boot sequence | shared/infra | active |
| `src/backupviewer/web/js/scan_ui.js` | 399 | the fleet-scan modal: check picker built from the backend registry, find chips, live progress, grouped report | flag scanning | active |
| `src/backupviewer/web/js/settings_ui.js` | 468 | the gear dialog (display/preferences tabs) and BV.uiPrefs.apply — fonts, sizes, glass/frost fills, bgfx dials | shared/infra | active |
| `src/backupviewer/web/js/sim_export.js` | 188 | the load-cameras picker: copies CV-X workspaces from backups into the simulator folder, foreign-dir guard | cameras | active |
| `src/backupviewer/web/js/state.js` | 65 | BV.state: open-backup manifest, per-backup tabData buckets, pub/sub, BV.tabState and BV.persistScroll | shared/infra | active |
| `src/backupviewer/web/js/tabs/alarms.js` | 119 | alarm-history section embedded in overview: 15-row snapshot per ERR*.LS file, expandable to a filtered vtable | backup parsing | active |
| `src/backupviewer/web/js/tabs/compare.js` | 491 | compare tab: changes-only two-backup report by category, hideable rows, lazy mini-diffs, backup picker flow | compare engine | active |
| `src/backupviewer/web/js/tabs/dcs.js` | 542 | dcs tab: signature dashboard, section menu, pendant-style section pages; owns the shared status pill map | 3D viewer | active |
| `src/backupviewer/web/js/tabs/edit.js` | 2343 | the #edit multi-robot .ls workspace: split panes, working-set rail, find/replace, live pane diff, export | program editor | active |
| `src/backupviewer/web/js/tabs/files.js` | 133 | raw file browser tab: virtualized list with ext filter, text/hex preview, and a camera-remote button | backup parsing | active |
| `src/backupviewer/web/js/tabs/frames.js` | 250 | frames tab: pendant-style tool/uframe/jog/payload cards per motion group, with show-empty and vs mode | backup parsing | active |
| `src/backupviewer/web/js/tabs/home.js` | 2277 | the #home library screen: plant/line/robot tree, per-row actions, batch ftp backup, discover, cam tiles | library | active |
| `src/backupviewer/web/js/tabs/io.js` | 336 | io tab: pendant-style signal browser by category, in/out panes, rack/slot/port config view, vs mode | backup parsing | active |
| `src/backupviewer/web/js/tabs/macros.js` | 113 | macro table (name, program, assignment) rendered inside the programs tab, with side-by-side vs mode | backup parsing | active |
| `src/backupviewer/web/js/tabs/mhvalves.js` | 227 | mh valves tab: gripper/valve setup cards from MHGRIPDT.VA with resolved io links, magnet section, full tree | backup parsing | active |
| `src/backupviewer/web/js/tabs/overview.js` | 653 | overview dashboard: hero plus draggable persisted cards (mastering, memory, ethernet, tasks) + date picker | backup parsing | active |
| `src/backupviewer/web/js/tabs/pdiff.js` | 86 | hidden #pdiff route wrapping BV.pdiffView: program-vs-program line diff, jump buttons, workspace add buttons | compare engine | active |
| `src/backupviewer/web/js/tabs/photos.js` | 533 | photos tab: camera image viewer (hero + lazy grid + zoom lightbox, cv-x height blend) and linked-camera list | cameras | active |
| `src/backupviewer/web/js/tabs/programs.js` | 960 | programs tab: tp/karel list with filters, style table, source detail, call tree, label xref, workspace picks | program editor | active |
| `src/backupviewer/web/js/tabs/registers.js` | 200 | registers tab: r/pr/sr sub-tabs in split or vs tables, hide-empty toggle, click-through to backup search | backup parsing | active |
| `src/backupviewer/web/js/tabs/search.js` | 158 | hidden #search route rendering backup-wide hits grouped by programs, io, registers, frames, macros, files | backup parsing | active |
| `src/backupviewer/web/js/tabs/sysvars.js` | 193 | system vars tab: lazy collapsible $-variable tree with source-file tags; exports treeNode for other tabs | backup parsing | active |
| `src/backupviewer/web/js/tabs/view3d.js` | 952 | 3d view tab: svg-projected dcs zones, orbit/pan/zoom + snap cube, posed fk arm, per-check side panel | 3D viewer | active |
| `src/backupviewer/web/js/theme.js` | 310 | theme data + apply layer: maps 9 theme colors onto css vars, hex/contrast math, the custom-theme color editor | theming | active |
| `src/backupviewer/web/js/theme_ui.js` | 269 | the theme picker row + drop panel: categories, credits, filter, hover-preview, edit/delete of custom themes | theming | active |
| `src/backupviewer/web/js/update.js` | 135 | release-check UI: boot autocheck toast, statusbar update pill, about-box updates row with skip-version | shared/infra | active |
| `src/backupviewer/web/js/util.js` | 395 | boots window.BV: esc/el/fmt/toast/copy, host-window fullscreen, modal+dirtyGuard, menu/dropPanel, collapsible | shared/infra | active |
| `src/backupviewer/web/js/workspace.js` | 383 | the multi-robot edit working set (BV.workspace): entry ids, buffers, persisted drafts, export payload | program editor | active |
| `src/backupviewer/web/themes/*.json (28 files)` | 448 | 28 bundled read-only theme packs, each {id,name,category,colors{9 hex}} in monkeytype/sports/cyberpunk/vibes | theming | active |
| `src/libraryimporter/__init__.py` | 6 | version + APP_NAME for the separate Library Importer app, and the note on where its brand strings live | LibraryImporter | active |
| `src/libraryimporter/api.py` | 164 | js_api bridge for the importer: pick source/dest, seed, and one canonical _state() payload per call | LibraryImporter | active |
| `src/libraryimporter/app.py` | 216 | pywebview window boot for the importer incl. a slimmed WebView2 software-rendering rescue relaunch | LibraryImporter | active |
| `src/libraryimporter/core.py` | 302 | stdlib seeding core: parse a robot list, plan it against a plant folder, write schema-2 robot.json skeletons | LibraryImporter | active |
| `src/libraryimporter/web/css/importer.css` | 162 | importer stylesheet: one hardcoded dark palette copied from the app's theme values, no themes or scaling | LibraryImporter | active |
| `src/libraryimporter/web/index.html` | 62 | importer's single page: drop zone, destination picker, line/robot checklist, go bar, result panel | LibraryImporter | active |
| `src/libraryimporter/web/js/api.js` | 51 | importer's promise wrapper over pywebview.api with the {ok,data} envelope, minus the app's busy/dedupe code | LibraryImporter | active |
| `src/libraryimporter/web/js/checklist.js` | 100 | importer's copy of BV.checklist: shift-range multiselect controller with tri-state select-all group boxes | LibraryImporter | active |
| `src/libraryimporter/web/js/importer.js` | 281 | the importer page logic: renders python's whole state, owns selection, runs seeding and the result panel | LibraryImporter | active |
| `src/libraryimporter/web/js/util.js` | 41 | importer's BV namespace with just esc, el and toast, trimmed from the main app's util.js | LibraryImporter | active |
| `tests/conftest.py` | 46 | pytest session fixtures for the three local-only sample-backup format trees plus a text reader *(untracked, local-only)* | tests | active |
| `tests/libraryimporter_probe.py` | 260 | end-to-end probe of the libraryimporter app: drop, checklist ranges, import, sidecars on disk | tests | active |
| `tests/perf_probe.py` | 255 | plant-scale timing probe: 2400 stubbed rows with ms budgets for notes, stars, shift-range and picker — the library screen's only performance-cliff guard | tests | active |
| `tests/probeutil.py` | 87 | the probes' shared preamble: temp-APPDATA/watcher isolation before any app import, plus check/js/poll and the FAILURES list all nine used to carry their own copy of | tests | active |
| `tests/test_alarms.py` | 36 | pytest for the alarm-history parser over the private fixture's ERRALL/ERRACT/ERRHIST/ERRMOT dumps *(untracked, local-only)* | tests | active |
| `tests/test_backuplog.py` | 118 | pytest for the backup-run log: run lifecycle, retry rejoining a closed run, 20-run cap, no password on disk | tests | active |
| `tests/test_camera_link.py` | 175 | pytest for library camera-to-robot auto-linking, taught camera names, and links surviving a rescan | tests | active |
| `tests/test_close_guard.py` | 61 | pytest for the app-close confirm guard: asks only while backups run, and fails open when the dialog throws | tests | active |
| `tests/test_compare.py` | 247 | pytest for every compare diff category plus cross-backup integration through the api *(untracked, local-only)* | tests | active |
| `tests/test_compare_align.py` | 105 | pytest for identity-normalized program alignment: equiv classification, junk blanks, banner comments | tests | active |
| `tests/test_cvx_image.py` | 281 | pytest for the cv-x bmp decoder: 15-bit height packing, no-data sentinel, pairing, and the photos endpoint | tests | active |
| `tests/test_cvx_inspect.py` | 162 | pytest for reading a cv-x camera's own name out of inspect.dat and teaching it into the library | tests | active |
| `tests/test_cvx_remote.py` | 417 | pytest for the cv-x remote wire protocol: framing, ctx echo, jpeg harvest, frame-ack, mouse encode and reorder, plus the firewall assertion that the bundled handshake blobs carry only documentation addresses | tests | active |
| `tests/test_cvx_window.py` | 266 | pytest for cv-x remote session lifecycle at the api: adopt, reload, pop-out window, host-window fullscreen | tests | active |
| `tests/test_dcs.py` | 180 | pytest for parse_dcs_report over real DCSVRFY/DCSCHGD dumps: sections, signatures, frames, user models *(untracked, local-only)* | tests | active |
| `tests/test_dcszones.py` | 395 | pytest for dcszones: DCSPOS.VA geometry, DCSVRFY.DG merge, user models, and disabled/uninit honesty | tests | active |
| `tests/test_discover.py` | 472 | pytest for backup-folder detection and the offline network scan: fanuc ftp, matrox ethernet/ip, adapters | tests | active |
| `tests/test_export_endpoint.py` | 299 | pytest for the path-addressed ws_* workspace endpoints: listing, reading, diffing, all-or-nothing export | tests | active |
| `tests/test_frames.py` | 48 | pytest for the tool/user/jog frame model built from SYSFRAME.VA plus FRAMEVAR.VA comments *(untracked, local-only)* | tests | active |
| `tests/test_ftpbackup.py` | 272 | pytest for the fanuc ftp pull against a fake controller: disk layout, completion marker, library register | tests | active |
| `tests/test_healthscan.py` | 843 | pytest for every fleet health check plus the scan job, driven by synthetic file texts through a fake session | tests | active |
| `tests/test_io.py` | 51 | pytest for merged IOCONFIG/IOSTATE signals: states, rack/slot/port ranges, pendant short names *(untracked, local-only)* | tests | active |
| `tests/test_io_fallback.py` | 53 | pytest for io rebuilt from SUMMARY.DG sections when IOCONFIG/IOSTATE are absent, plus the FLG column split *(untracked, local-only)* | tests | active |
| `tests/test_keyence_workspace.py` | 317 | pytest pinning workspace.xml byte-for-byte plus export naming and the ledger guarding foreign folders | tests | active |
| `tests/test_keyencebackup.py` | 283 | pytest for the cv-x ftp pull against a fake camera: cwd-per-dir quirk, scope, multi-cam, workspace manifest | tests | active |
| `tests/test_kinematics.py` | 222 | pytest for the .def parse, FK chain poses, flange measurement, and curpos/tool-frame reading | tests | active |
| `tests/test_label_xref.py` | 44 | pytest for LBL definitions and JMP xref in .ls programs, including broken jumps and comment lookalikes | tests | active |
| `tests/test_library.py` | 183 | pytest for the library registry overlay: add/update/bulk-add dedupe, register_backup matching, partial-never-latest, resolve_open_path fallbacks | tests | active |
| `tests/test_library_relocate.py` | 987 | pytest for relocate_robot/merge_robots: transactional folder moves, alias recording, duplicate-vs-conflict rules, evidence-based merge suggestions with F-number veto | tests | active |
| `tests/test_library_scan.py` | 684 | pytest for scan_library_root's files-are-law rules: disk wins over overlay, schema-3 sidecars, stale/absorbed reporting, lib_list signature caching | tests | active |
| `tests/test_libraryimporter_app.py` | 135 | pytest for the LibraryImporter second app: its WebView2 failure-watch/relaunch ladder and the Api bridge envelope, seed and drag-drop handlers | tests | active |
| `tests/test_libraryimporter_core.py` | 209 | pytest for the importer's parse -> plan -> seed pipeline, full-name expansion, schema-2 sidecar shape and destination sanity warnings | tests | active |
| `tests/test_libraryimporter_integration.py` | 63 | pytest proving a tree seeded by libraryimporter.core is adopted by BackupViewer's scanner with path identity, IPs and stable ids | tests | active |
| `tests/test_ls_edit.py` | 342 | pytest for the .LS edit/export engine: byte-faithful decode/encode, section split, emit renumbering, /ATTR and /POS splices, program rename | tests | active |
| `tests/test_macros.py` | 15 | pytest for the SYSMACRO.VA macro table: names, program names and DI assignments *(untracked, local-only)* | tests | active |
| `tests/test_magnet.py` | 67 | pytest for magnet-EOAT detection from MAG*.PC programs and the R[800-899] register grouping | tests | active |
| `tests/test_mhvalves.py` | 151 | pytest for MH valve signal-table resolution and the no-phantom-vacuum guard on controller defaults | tests | active |
| `tests/test_modeldb.py` | 95 | pytest for modeldb import/normalized matching/builtin-vs-imported layering plus a wellformedness sweep of the shipped kinematics BUILTIN table | tests | active |
| `tests/test_mtx_remote.py` | 196 | pytest for the Matrox web-UI remote: DesignAssistant page scraping and mtx_remote_start/mtx_remote_window endpoints with the HTTP probe faked | tests | active |
| `tests/test_mtxbackup.py` | 383 | pytest for the Matrox SMB camera pull end-to-end against a temp camera home via an injected mount, incl. MAX_PATH copy/index and camera self-naming | tests | active |
| `tests/test_payloads.py` | 43 | pytest for payload schedules out of $PLST_GRP: mass/cg/inertia plus uninit and -9999 sentinel flagging | tests | active |
| `tests/test_phone_view.py` | 343 | pytest driving PhoneShare over real loopback HTTP with the camera fetch faked, plus address ranking and the phone_view_* endpoints and firewall helper | tests | active |
| `tests/test_probes.py` | 98 | the wiring that makes pytest run the nine probes: one subprocess each, timeout, `probe` marker (deselected by default in pyproject) | tests | active |
| `tests/test_programs.py` | 77 | pytest for .ls header/body/position parsing, call hops, and KAREL .PC listing over the private fixture *(untracked, local-only)* | tests | active |
| `tests/test_qr.py` | 218 | pytest pinning the hand-rolled QR encoder: Reed-Solomon syndromes, BCH format info, spec geometry, and a from-scratch reader that re-reads the payload | tests | active |
| `tests/test_registers.py` | 38 | pytest for NUMREG/POSREG/STRREG parsing: counts, joint vs cartesian PRs, uninit slots *(untracked, local-only)* | tests | active |
| `tests/test_screengrab.py` | 125 | pytest decoding the app's own PNG output by hand plus Windows GDI window-capture smoke tests and a pointer-sized HWND prototype guard | tests | active |
| `tests/test_session.py` | 63 | pytest for BackupSession: manifest, .ls program-vs-report split, case-insensitive find, karel detection *(untracked, local-only)* | tests | active |
| `tests/test_session_formats.py` | 76 | pytest for multi-format sessions: MD vs maintenance vs all-above detection, dedupe, per-format tabs *(untracked, local-only)* | tests | active |
| `tests/test_sessions.py` | 198 | pytest for the multi-session registry behind backup tabs: open/switch/close, per-entry compare, session cap, pop-out drop, sid/side parameter-order guard | tests | active |
| `tests/test_settings.py` | 48 | pytest that settings._write retries a transient Windows PermissionError on the atomic replace and still raises when the lock never clears | tests | active |
| `tests/test_sim_export.py` | 201 | pytest for loading camera workspaces into the CV-X simulator's flat folder: what is offered, naming, blocking hand-made workspaces, sim_root setting | tests | active |
| `tests/test_summary.py` | 67 | pytest for parse_summary over a real SUMMARY.DG: identity, options, memory, ethernet, tasks, macros *(untracked, local-only)* | tests | active |
| `tests/test_sysvar_merge.py` | 157 | pytest for merge_system_records stitching [*SYSTEM*] records across many .VA files, excluding register/KAREL sections, through to get_sysvar_records | tests | active |
| `tests/test_sysvars.py` | 91 | pytest for the sysvar record tree and flatten, plus KAREL structs whose field lines carry no $ prefix *(untracked, local-only)* | tests | active |
| `tests/test_updatecheck.py` | 130 | pytest for updatecheck: version parsing/ordering, check()'s honest per-failure statuses with fetch injected, and the frozen-only autocheck policy | tests | active |
| `tests/test_v02_parsers.py` | 152 | pytest grab-bag for callgraph, gmwizlog, mastering, styles, io pendant names and the search engine *(untracked, local-only)* | tests | active |
| `tests/test_va_tokenizer.py` | 51 | pytest for the .VA engine: scalar coercion, scalar arrays, and position arrays incl. a synthetic joint block *(untracked, local-only)* | tests | active |
| `tests/test_viewfinder.py` | 188 | pytest for the window-mirroring phone share: PhoneShare.start_window_session over loopback plus viewfinder_start choosing which of our own windows to mirror | tests | active |
| `tests/test_webview_boot.py` | 90 | pytest for the WebView2 0x8007139F boot rescue: failure watch, two-way relaunch ladder, software-rendering fallback env and storage dir | tests | active |
| `tests/ui_batch_probe.py` | 1442 | hidden-window probe: library home rows, note editing, cam lens, program navigator, mh-valve reflow, tab keys | tests | active |
| `tests/ui_bgfx_probe.py` | 453 | hidden-window probe of bgfx effects, the settings dialog's two tabs and the theme picker panel | tests | active |
| `tests/ui_cvxremote_probe.py` | 231 | hidden-window probe of the cv-x remote bar, top-bar phone button and pop-out session adoption | tests | active |
| `tests/ui_edit_probe.py` | 1398 | hidden-window probe of the #edit workspace: panes, tab strips, find/replace, pane diff, per-robot export | tests | active |
| `tests/ui_probe.py` | 2108 | the original full-app probe: boots a real local backup and walks every tab, primitive and compare view *(untracked, local-only)* | tests | active |
| `tests/ui_sim_export_probe.py` | 317 | hidden-window probe of the cv-x simulator-folder settings row and the load-cameras picker guard | tests | active |
| `tests/ui_tabs_probe.py` | 305 | hidden-window probe of backup tabs: strip, per-backup memory, tear-off, and the solo pop-out window | tests | active |
| `tests/ui_updatecheck_probe.py` | 204 | hidden-window probe of the release check ui: statusbar pill, about updates row, skip and startup toggle | tests | active |
| `tools/apply_ip_list.py` | 159 | cli that stamps a {line:{robot:ip}} list onto library folders as robot.json sidecars, dry-run by default | tools/scripts | active |
| `tools/restyle.py` | 459 | standalone kit builder cloning s<from>*/style<from>* .ls programs to a new style number, rewriting call refs | tools/scripts | active |
| `tools/seed_library.py` | 176 | hand-out cli expanding a short-name robot list into library plant/line/robot folders with robot.json sidecars | tools/scripts | active |
| `robot modelas/_re/RMD-FORMAT.md` | 119 | reverse-engineered spec for keyence .rmd robot-model files: container, part records, bvh, mesh soup, chain *(untracked, local-only)* | 3D viewer | active |
| `robot modelas/_re/rmd.py` | 131 | reference stdlib parser for .rmd files: metadata, per-link mesh slices, and home/posed link transforms *(untracked, local-only)* | 3D viewer | active |
| `robot modelas/_re/rmd_assemble.py` | 165 | brute-forces the chain composition convention by link-proximity score and renders the winning pose to png *(untracked, local-only)* | 3D viewer | active |
| `robot modelas/_re/rmd_capsulefit.py` | 45 | measures how well one circumscribed capsule per link approximates that link's .rmd mesh *(untracked, local-only)* | 3D viewer | active |
| `robot modelas/_re/rmd_png3.py` | 87 | renders per-link .rmd wireframe panels to a png strip and prints each record's bounding box *(untracked, local-only)* | 3D viewer | active |
| `robot modelas/_re/rmd_validate.py` | 181 | runs every confirmed .rmd format claim (sizes, soup order, normals, bvh tiling, assembly gaps) over the corpus *(untracked, local-only)* | 3D viewer | active |

---

## 1. Subsystems found

| subsystem | files | ~lines |
|---|---:|---:|
| tests | 66 | 18,233 |
| shared/infra | 27 | 7,612 |
| program editor | 8 | 6,383 |
| theming | 34 | 5,657 |
| backup parsing | 31 | 5,601 |
| library | 4 | 4,247 |
| 3D viewer | 16 | 3,832 |
| cameras | 9 | 2,559 |
| docs | 6 | 2,319 |
| remote/mobile | 10 | 2,032 |
| backup capture | 4 | 1,739 |
| flag scanning | 2 | 1,422 |
| LibraryImporter | 11 | 1,395 |
| compare engine | 5 | 1,353 |
| tools/scripts | 3 | 794 |
| build/config | 7 | 251 |
| **total** | **243** | **65,429** |

> Counts are by *primary* subsystem only — a file appears once, so these add up to the
> whole repo. The `tests` row is the largest because every probe and unit suite counts as
> `tests` rather than as the feature it exercises — each test row in the map names its own
> target. Read the other rows as *production* size: `shared/infra` is inflated by `api.py`
> alone (3,512 lines), and `theming` by `components.css` (2,023), which is really the whole
> app's stylesheet.

---

## 2. Files that belong to more than one subsystem

These 73 source files genuinely straddle two areas — either they serve two features,
or they sit in one layer's folder while doing another layer's job. Sorted by primary.

*(The 66 test files each also carry a "feature under test" tag. That is a test pointing at
its target, not dual membership, so they are omitted here — the map above has each one.)*

| path | primary | also | what it is |
|---|---|---|---|
| `CVX_FTP_LAYOUT.md` | docs | cameras | field notes on the cv-x ftp tree and simulator workspace layout, and what env.dat cannot prove |
| `edit_sandbox.html` | program editor | tests | untracked browser rig driving the real lseditor component against the real css, asserting… |
| `run.py` | build/config | shared/infra | dev launcher and pyinstaller entry script: puts src on sys.path, calls backupviewer.app.main |
| `run_libraryimporter.py` | LibraryImporter | build/config | dev launcher and pyinstaller entry script for the companion libraryimporter app |
| `split_diff_sandbox.html` | program editor | compare engine | untracked ui mock of the 4-pane split tree and inline pane diff, with a mock aligner and review modal |
| `packaging/libraryimporter.spec` | build/config | LibraryImporter | pyinstaller onefile spec for libraryimporter.exe, bundling src/libraryimporter/web, no icon |
| `src/backupviewer/__init__.py` | shared/infra | build/config | package marker holding the single source of the app version string (1.4) |
| `src/backupviewer/api.py` | shared/infra | backup parsing | the pywebview bridge class: 117 @_endpoint methods returning {ok,data} envelopes across every… |
| `src/backupviewer/compare.py` | compare engine | program editor | pure two-backup diff functions: io/registers/frames/payloads/programs rows plus tp-line alignment |
| `src/backupviewer/cvx_handshake/chan8502_tx.bin` | remote/mobile | cameras | captured cv-x 8502 control-channel client handshake: 13 messages incl. six 16 kb blobs, replayed at… |
| `src/backupviewer/cvx_handshake/chan8503_tx.bin` | remote/mobile | cameras | captured cv-x 8503 aux-channel handshake: a single channel-open message replayed to open the aux socket |
| `src/backupviewer/cvx_handshake/chan8504_tx.bin` | remote/mobile | cameras | captured cv-x 8504 video-channel handshake: channel-open, video-service open and the frame-ack prime… |
| `src/backupviewer/cvx_remote.py` | remote/mobile | cameras | cv-x remote-desktop client: handshake replay on 3 sockets, jpeg frame harvest, mouse events, mjpeg… |
| `src/backupviewer/discover.py` | backup capture | cameras | subnet scan job finding fanuc/keyence over ftp and matrox via ethernet/ip, plus adapter list and… |
| `src/backupviewer/ftpbackup.py` | backup capture | cameras | ftp backup engine: gentle md: pull, dated+latest tree, .part/complete-marker crash safety, shared… |
| `src/backupviewer/healthscan.py` | flag scanning | backup parsing | fleet health-scan engine: 17-check registry, lazy per-robot parse context, threaded job, fleet-wide… |
| `src/backupviewer/keyencebackup.py` | cameras | backup capture | cv-x camera backup job over anonymous ftp, plus pre-flight probe, read-only diagnose and self-naming |
| `src/backupviewer/library.py` | library | backup capture | persistent robot library in %appdata%: folder-tree-as-truth scan, robot.json sidecars,… |
| `src/backupviewer/mtxbackup.py` | cameras | backup capture | matrox camera backup job over smb: wnet+credential login, da tree and newest images copy, probe and… |
| `src/backupviewer/parsers/__init__.py` | backup parsing | shared/infra | parsers package init holding TAB_REQUIREMENTS, the map of which backup files make each ui tab available |
| `src/backupviewer/parsers/callgraph.py` | backup parsing | flag scanning | extracts CALL/RUN/macro edges from .LS bodies into a call graph plus per-line clickable jump hops |
| `src/backupviewer/parsers/curpos.py` | 3D viewer | backup parsing | parses CURPOS.DG per-group joint/world pose and FRAME.DG tool-frame rows used to pose the 3d robot |
| `src/backupviewer/parsers/cvx_inspect.py` | cameras | library | reads the inspection program name from a cv-x inspect.dat (two copies must agree) and elects a… |
| `src/backupviewer/parsers/dcs.py` | backup parsing | 3D viewer | parses DCSVRFY/DCSCHGD/DCSDIFF .DG verify reports into classified sections, signatures and… |
| `src/backupviewer/parsers/dcszones.py` | 3D viewer | backup parsing | builds the 3d zone payload from DCSPOS.VA shadow tables plus the DCSVRFY.DG report: zones, frames,… |
| `src/backupviewer/parsers/ls_program.py` | backup parsing | program editor | parses a .LS tp program: /PROG and /ATTR header, body lines, /POS points and lbl/jmp label… |
| `src/backupviewer/parsers/mtx_portal.py` | cameras | remote/mobile | scrapes a matrox camera portal's html for DesignAssistant operator page urls (hrefs or prj-name rows) |
| `src/backupviewer/parsers/summary_dg.py` | backup parsing | backup capture | parses SUMMARY.DG pseudo-html into identity, options, motors, memory, tasks, safety, positions,… |
| `src/backupviewer/parsers/sysvars.py` | backup parsing | compare engine | merges [*SYSTEM*] records across the ~two dozen carrier .VA files and expands one into a tree or… |
| `src/backupviewer/qr.py` | remote/mobile | shared/infra | hand-rolled qr encoder (byte mode, ec level l, versions 1-5) returning a 0/1 module matrix |
| `src/backupviewer/search.py` | backup parsing | flag scanning | backup-wide search: type[n] signal queries vs free text across programs, io, registers, frames,… |
| `src/backupviewer/session.py` | backup parsing | cameras | BackupSession: recursive case-insensitive file index, lazy parse cache, ls/karel classify,… |
| `src/backupviewer/web/css/base.css` | theming | shared/infra | root css: theme variable contract (--bg/--accent/--edge/--panel), app shell layout, chrome bars,… |
| `src/backupviewer/web/css/components.css` | theming | shared/infra | the app's single component stylesheet: cards, tables, pills, modals, plus per-tab styles for every… |
| `src/backupviewer/web/fonts/Orbitron-VariableFont_wght.ttf` | theming | build/config | bundled orbitron variable font, loaded by base.css @font-face and offered as the 'rog' ui font in… |
| `src/backupviewer/web/js/components/checklist.js` | shared/infra | library | BV.checklist: the one multiselect controller — shift-click ranges, tri-state group boxes,… |
| `src/backupviewer/web/js/components/framecard.js` | shared/infra | backup parsing | BV.frameCard: the tool/uframe card — title, status pills, subtitle, xyzwpr list and config line |
| `src/backupviewer/web/js/components/icons.js` | shared/infra | theming | BV.icon: 4 inline stroke-svg glyphs (phone/gear/help/remote) plus a boot sweep filling [data-icon]… |
| `src/backupviewer/web/js/components/pdiffview.js` | compare engine | program editor | BV.pdiffView: aligned side-by-side program-line diff renderer with equiv rows, stats pills and… |
| `src/backupviewer/web/js/components/vsdiff.js` | compare engine | shared/infra | BV.vsDiff: highlight-diffs toggle plus row-tint markers and io/register/program/macro field comparators |
| `src/backupviewer/web/js/cvxremote.js` | remote/mobile | cameras | cv-x remote overlay: mjpeg screen mirror plus full mouse forwarding, session adopt/rebind for pop-outs |
| `src/backupviewer/web/js/highlight_tp.js` | shared/infra | program editor | regex tokenizer that wraps FANUC TP program lines in tp-* spans for themed syntax highlighting |
| `src/backupviewer/web/js/jobs.js` | backup capture | shared/infra | BV.jobs: 500ms backup-job poller, run-wide progress jobstrip with live details panel, global busy… |
| `src/backupviewer/web/js/manage_ui.js` | library | backup capture | the manage-backups modal: last-run log with retry-failed, partial-snapshot review, stale list, tidy… |
| `src/backupviewer/web/js/mtxremote.js` | remote/mobile | cameras | matrox remote overlay: sandboxed iframe tabs of the camera's own web ui, window fallback if unframeable |
| `src/backupviewer/web/js/scan_ui.js` | flag scanning | library | the fleet-scan modal: check picker built from the backend registry, find chips, live progress,… |
| `src/backupviewer/web/js/settings_ui.js` | shared/infra | theming | the gear dialog (display/preferences tabs) and BV.uiPrefs.apply — fonts, sizes, glass/frost fills,… |
| `src/backupviewer/web/js/tabs/dcs.js` | 3D viewer | compare engine | dcs tab: signature dashboard, section menu, pendant-style section pages; owns the shared status pill map |
| `src/backupviewer/web/js/tabs/edit.js` | program editor | compare engine | the #edit multi-robot .ls workspace: split panes, working-set rail, find/replace, live pane diff, export |
| `src/backupviewer/web/js/tabs/files.js` | backup parsing | remote/mobile | raw file browser tab: virtualized list with ext filter, text/hex preview, and a camera-remote button |
| `src/backupviewer/web/js/tabs/frames.js` | backup parsing | compare engine | frames tab: pendant-style tool/uframe/jog/payload cards per motion group, with show-empty and vs mode |
| `src/backupviewer/web/js/tabs/home.js` | library | backup capture | the #home library screen: plant/line/robot tree, per-row actions, batch ftp backup, discover, cam tiles |
| `src/backupviewer/web/js/tabs/io.js` | backup parsing | compare engine | io tab: pendant-style signal browser by category, in/out panes, rack/slot/port config view, vs mode |
| `src/backupviewer/web/js/tabs/macros.js` | backup parsing | compare engine | macro table (name, program, assignment) rendered inside the programs tab, with side-by-side vs mode |
| `src/backupviewer/web/js/tabs/overview.js` | backup parsing | library | overview dashboard: hero plus draggable persisted cards (mastering, memory, ethernet, tasks) + date… |
| `src/backupviewer/web/js/tabs/pdiff.js` | compare engine | program editor | hidden #pdiff route wrapping BV.pdiffView: program-vs-program line diff, jump buttons, workspace add… |
| `src/backupviewer/web/js/tabs/photos.js` | cameras | remote/mobile | photos tab: camera image viewer (hero + lazy grid + zoom lightbox, cv-x height blend) and… |
| `src/backupviewer/web/js/tabs/programs.js` | program editor | compare engine | programs tab: tp/karel list with filters, style table, source detail, call tree, label xref,… |
| `src/backupviewer/web/js/tabs/registers.js` | backup parsing | compare engine | registers tab: r/pr/sr sub-tabs in split or vs tables, hide-empty toggle, click-through to backup search |
| `src/backupviewer/web/js/tabs/search.js` | backup parsing | shared/infra | hidden #search route rendering backup-wide hits grouped by programs, io, registers, frames, macros,… |
| `src/backupviewer/web/js/theme.js` | theming | shared/infra | theme data + apply layer: maps 9 theme colors onto css vars, hex/contrast math, the custom-theme… |
| `src/libraryimporter/__init__.py` | LibraryImporter | build/config | version + APP_NAME for the separate Library Importer app, and the note on where its brand strings live |
| `src/libraryimporter/api.py` | LibraryImporter | shared/infra | js_api bridge for the importer: pick source/dest, seed, and one canonical _state() payload per call |
| `src/libraryimporter/app.py` | LibraryImporter | shared/infra | pywebview window boot for the importer incl. a slimmed WebView2 software-rendering rescue relaunch |
| `src/libraryimporter/core.py` | LibraryImporter | library | stdlib seeding core: parse a robot list, plan it against a plant folder, write schema-2 robot.json… |
| `src/libraryimporter/web/css/importer.css` | LibraryImporter | theming | importer stylesheet: one hardcoded dark palette copied from the app's theme values, no themes or scaling |
| `src/libraryimporter/web/js/api.js` | LibraryImporter | shared/infra | importer's promise wrapper over pywebview.api with the {ok,data} envelope, minus the app's… |
| `src/libraryimporter/web/js/checklist.js` | LibraryImporter | shared/infra | importer's copy of BV.checklist: shift-range multiselect controller with tri-state select-all group… |
| `src/libraryimporter/web/js/util.js` | LibraryImporter | shared/infra | importer's BV namespace with just esc, el and toast, trimmed from the main app's util.js |
| `tools/apply_ip_list.py` | tools/scripts | library | cli that stamps a {line:{robot:ip}} list onto library folders as robot.json sidecars, dry-run by default |
| `tools/restyle.py` | tools/scripts | program editor | standalone kit builder cloning s<from>*/style<from>* .ls programs to a new style number, rewriting… |
| `tools/seed_library.py` | tools/scripts | library | hand-out cli expanding a short-name robot list into library plant/line/robot folders with robot.json… |
| `robot modelas/_re/RMD-FORMAT.md` | 3D viewer | docs | reverse-engineered spec for keyence .rmd robot-model files: container, part records, bvh, mesh soup,… |

---

## 3. What surprised us

Findings only — nothing here was acted on. Line references are to the working tree.

### A. Would break a fresh clone

- **`components/icons.js` is untracked but load-bearing.** It is `??` in git status, yet
  `index.html:93` loads it, `base.css:285` and `components.css:235-240` style `.bv-ico`,
  and four shipped files call `BV.icon()` (`cvxremote.js:68`, `mtxremote.js:47`,
  `tabs/files.js:58`, `tabs/photos.js:500`). On a clean checkout the three topbar cubes and
  the phone/remote buttons render as empty boxes. Worse, the **tracked** `ui_cvxremote_probe.py`
  (also modified in the tree) now asserts `svg.bv-ico` exists at lines 107-123 — so probe and
  component have to land in the same commit or the tracked probe fails on a clean checkout.
  > **✅ RESOLVED `b47b4bc`.** Tracked. It was the only untracked file among all 57 assets
  > `index.html` references. Committed alone; the icon *wiring* (index.html, the CSS, the four
  > callers, the probe assertions) is part of the separate UI-chrome batch still in the tree.
- **`ui_bgfx_probe.py` is stale against shipped code.** It asserts `BV.bgfx.EFFECTS.length == 13`
  (line 69) and 13 dropdown items (line 228); `bgfx.js` ships **19**. The six new effects landed
  in `e594785` on 2026-07-27, the probe was last touched 2026-07-25. Two checks fail today, and
  the per-effect param racks and real-`dt` work have no coverage at all.
  > **✅ RESOLVED `5285a17`** — and it was **four** stale checks, not two: the settings display
  > tab had also grown four global dials (speed / density / variance / hue drift) that
  > `display_rows` and `display_sliders` did not know about. `fx_menu_items` now compares
  > against `BV.bgfx.EFFECTS.length` live, since "the menu lists every effect" is the real
  > invariant; `boot.effect_count` keeps a literal as the deliberate did-we-lose-one anchor.
  > The coverage gaps are now recorded in the probe's own docstring — including that real-`dt`
  > is unreachable from a probe window (no `requestAnimationFrame`, so no frames ever advance)
  > without an injectable step in `bgfx.js`.
- **No probe is collected by pytest.** All nine are standalone `main()` scripts named
  `ui_*_probe.py` / `perf_probe.py` / `libraryimporter_probe.py` — none match `test_*.py`. So
  `python -m pytest tests -q` never runs one; each needs its own process and a hand invocation.
  > **✅ RESOLVED `da10c92`.** `tests/test_probes.py` runs each in its own subprocess with a
  > timeout, marked `probe`; `pyproject.toml` deselects that marker by default so the fast
  > suite stays fast. See **Verifying the app** in CLAUDE.md / README for the commands.
  > Three probes had rotted unnoticed and are now green: `ui_bgfx` (above), `ui_batch` (a
  > `#photo-hero > div` selector that went one level stale when `c8b3584` wrapped the figure —
  > the app was fine, checked against a clean checkout), and `perf_probe`, which **hung
  > forever** on a `webview.start` argument bug and so had not run in weeks.
- **Four git-excluded tests are fully synthetic and need no fixture** — `test_magnet.py`,
  `test_mhvalves.py`, `test_payloads.py` (plus roughly half of `test_va_tokenizer.py` and
  `test_sysvars.py`). They appear excluded by association with their real-data siblings rather
  than by need. That is free coverage a fresh clone is currently missing.
  > **✅ RESOLVED `85e147c`, partly — and the "roughly half" was optimistic.** The three named
  > files are tracked (11 tests; a bare clone went 574 → 585). They were swept against a
  > 5,012-term set built from `robots.json` + the `SampleBackup` tree + `conftest.py`: no IP,
  > F-number or robot-name shape. But `test_sysvars.py` takes the `sample_backup` fixture in
  > *every* test and asserts a real backup's record count (`len(recs) == 769`), and only one of
  > `test_va_tokenizer.py`'s three tests is synthetic. Both correctly stay excluded.

### B. Duplication and drift

- **`bgfx.js` declares four helpers twice inside one IIFE.** `var _varCache` at both line 25 and
  128; `rgbOf` at 26 and 129; `drift` at 143 and 199; `rotHue` at 147 and 203; `hue2c` at 163 and
  220 — and the hue-drift rationale comment is written out twice. Hoisting means the **second**
  copy wins for every caller, and the two `rotHue` bodies are *not* identical (the second adds an
  `if (!deg) return c;` fast path), so the first set is dead code. Reads like a merge leftover
  from the bgfx overhaul.
- **`libraryimporter/web/js/checklist.js` is a stale copy, not a deliberate trim.** It is the main
  app's `components/checklist.js` at an earlier revision. The main app now stores `items[key]` as
  an array of bound copies and uses `cb.checkVisibility()` — the fix for the 42-second plant-scale
  freeze; the importer copy still keeps a single `{cb, seq}` and reads `cb.offsetParent !== null`.
  Its two siblings (`util.js`, `api.js`) *are* honest trims with comments explaining the cuts.
  Only one of the two checklists got the perf lesson.
- **Library seeding exists three times.** `tools/seed_library.py` (hand-out CLI),
  `tools/apply_ip_list.py` (stamps IPs onto an existing tree) and `src/libraryimporter/core.py`
  each rebuild the same `robot.json` sidecar shape and name expansion; `core.py` says out loud
  that it lifted the code and copies `library.py`'s `_skip_name` regexes rather than importing
  them. Deliberate (the importer ships standalone), but a schema change now needs three edits.
- **The two camera remotes share ~60-70 of `mtxremote.js`'s 168 lines.** Same DOM shell, same CSS
  classes, same bar layout, same one-session guard, same esc handling, same error panel — only the
  payload differs (CV-X streams MJPEG and forwards mouse; MTX builds sandboxed iframe tabs). The
  codebase's own third-occurrence rule points at a `BV.remoteOverlay` primitive. They have already
  drifted: `cvxremote.js` installs a capture-phase guard over the digit/`-`/`=` tab keys so they
  cannot leak to `keys.js` behind a fullscreen remote; `mtxremote.js` has no such guard.
- **Two 2,000+ line tab files are several screens each.** `edit.js` (2,343) holds a recursive pane-
  tree layout engine, the working-set rail, rename/duplicate flows, a whole find/replace match
  engine, the live inline pane diff, a CALL/LBL/JMP navigator, and two modals. `home.js` (2,277)
  holds the library tree, the rich robot row, per-lens scroll memory, tidy/merge/move flows — plus
  a **multi-cam live-tile lens** (cameras) and **batch FTP backup + the two-step network discover
  flow** (backup capture) living inside the library tab.
- **Smaller real duplications:** `vsdiff.js:17-29` hand-builds a `.seg` button row instead of
  calling `BV.segmented`; `framecard.js:33-37` hand-writes `<dl class="kv">` markup though
  `BV.kv.html` exists and loads first; `router.js` keeps a private `emptyState()` while many tabs
  render their own.
- **Boot-rescue logic is mirrored across the two apps and has drifted.** The main app's
  `_next_boot_action` takes three args and relaunches both ways; the importer's takes two and
  relaunches once. `test_webview_boot.py` and `test_libraryimporter_app.py` both test it.
- **~40 lines of identical preamble in all nine probes** (`check()`/`js()`/`poll()`, temp-APPDATA +
  `BV_NO_WATCHER`, the `create_window(hidden=True)` tail). A `tests/probeutil.py` is the obvious
  promotion.
  > **✅ RESOLVED `8c094bf`** — `tests/probeutil.py`, −179 lines. `js()` had been byte-identical
  > in all nine; `check()` had drifted into two spellings of the same function and `poll()` into
  > four, differing only in patience (`poller()` keeps that where it was deliberate). **Window
  > creation deliberately did not move**: the probes differ in size, in whether they hand
  > pywebview a path or a `file://` URI, and in what they hang off the window afterwards.

### C. Names and comments that no longer match the code

- **`ftpbackup.py` badly understates itself.** Besides the FANUC `BackupJob` it owns the shared
  download/mirror primitives (`retrieve`, `mirror_latest`, `long_path`, `dated_dir`/`latest_dir`)
  and the `_JobBase`/`CameraJobBase` classes that the **Matrox (SMB)** and Keyence camera jobs
  inherit. Camera code and `library.py` both depend on a module named for FTP.
- **`tabs/alarms.js` and `tabs/macros.js` register no tab.** They export `BV.alarms.renderInto`
  and `BV.macros.render` — embedded panels inside overview and programs. Both say so in header
  comments, so it is intentional demotion, but the directory name is a half-truth for 2 of 18.
- **`sim_export.js` is not an export.** It is the "load cameras into the simulator" flow, reachable
  only from the preferences page in `settings_ui.js`.
- **`components.css` (2,023 lines) is the whole app's stylesheet**, not shared components — it
  carries edit-workspace, DCS, sysvars, pdiff, library, cameras, CV-X remote, 3D and settings
  rules. It also has a hand-maintained invariant: the `html.frosted` selector list (lines 50-62)
  must be extended for every new `--panel` surface or the opacity and frost sliders describe
  different sets of surfaces.
- **`backuptabs.js` is not a primitive.** It sits in `components/` but is a singleton bound to the
  concrete `#sessionbar` element and `BV.session` — app shell, not a reusable piece.
- **Stale docstrings/comments found (code is right, prose is wrong):** `parsers/__init__.py` still
  says "FANUC … text → dict" though the folder now holds byte parsers (`cvx_image`, `cvx_inspect`)
  and one that parses live HTTP HTML (`mtx_portal`); `dcs.py`'s KINDS list omits the `frames` and
  `user_model` kinds the code emits; `healthscan.py`'s category list is one behind `CHECKS`
  (`positions`); `view3d.js` (~line 803) says user models are "still data-only in the viewport"
  while `posedElements()`/`draw()` place and draw them; `photos.js` points at a "Cameras tab" that
  is not a separate file; `theme_ui.js` says 29 themes when 28 ship; `cvx_remote.py`'s docstring
  cites `CvxRemote/mirror2.cs` and `CVX_REMOTE_HANDOFF.md`, neither of which is in the repo.
- **README's module tree is partial** — it omits `compare.py`, `discover.py`, `search.py`,
  `phoneview.py`, `qr.py`, `screengrab.py`, `keyence_workspace.py`, `kinematics_builtin.py` and
  `modeldb.py`. Nothing in it is wrong; a reader just underestimates the module count.
- **`packaging/libraryimporter.spec` still warns off the Microsoft Store Python** — a warning the
  sibling `backupviewer.spec` explicitly retired in `2d09bca`, and which `build_exe.log`
  independently disproves (clean Store-Python 3.13 / PyInstaller 6.20 build).

### D. Orphans, layering, and things worth a second look

- **All three `*_sandbox.html` rigs are stale.** Nothing in the repo references
  `edit_sandbox.html`, `edit_workspace_sandbox.html` or `split_diff_sandbox.html` (grepped), and
  all three are superseded by shipped code (`components/lseditor.js`, `workspace.js` + `tabs/edit.js`,
  `components/pdiffview.js`). `edit_sandbox.html`'s own header says it "dies when the component is
  trusted". They are untracked, so they cost the public repo nothing.
- **`BV.openPhoneView` (`phoneview.js:37`) has no callers** — grep returns only its definition and
  a comment saying it is "kept for callers that want it". Every live phone button goes through
  `BV.openViewfinder`. This confirms the earlier composition audit.
- **Two layer inversions.** `session.py` (parsing) imports `long_path` from `ftpbackup.py`
  (capture) just for the `\\?\` helper; `healthscan.py` imports the private `_ScanJob` base from
  `discover.py`, dragging in ftplib, sockets, EtherNet/IP and subprocess for a progress/cancel base
  class that has nothing to do with networking.
- **`api.py` is 3,512 lines and 117 endpoints** — the single hub, importing nearly every module and
  ~25 parsers. It touches every subsystem on this list except LibraryImporter, docs, tests and build.
- **Two `.LS` readers coexist by design and say so:** `ls_program.py` decodes cp1252 for display
  (lossy), `ls_edit.py` decodes latin-1 for a lossless byte round-trip. Each carries its own
  body/POS regex set, so a format fix has to be made in both.
- **Invisible load-order coupling.** `dcs.js:174-175` assigns `BV.dcsDetail` / `BV.dcsStatusPill`
  and `view3d.js` consumes them in five places — `index.html` loads dcs at 119 and view3d at 127,
  so swapping those two `<script>` tags breaks the 3D side panel. Separately, the **order of the
  `tabs/*.js` tags is the number-key map**: `router.js` badges tabs 1-9 then `-`/`=` in `BV.tabs`
  order, so reordering script tags silently re-maps keyboard shortcuts.
- **Cross-test imports.** `test_discover.py` imports `_make_camera`/`_mount_factory` from
  `test_mtxbackup`, and `test_modeldb.py` imports `_DEF` from `test_kinematics.py`. Both resolve
  only because pytest puts `tests/` on `sys.path`.
- **Near-identical test names testing different things:** `test_sessions.py` (tracked — Api's
  multi-session registry) vs `test_session.py` (git-excluded — `BackupSession`'s file index against
  the private fixture). One letter apart, adjacent when sorted; a clone silently has half.
- **`search.py` caps dishonestly.** `MAX_ROWS` `break`s only the inner per-kind loop, so register
  hits can exceed the cap, and `total` is summed *after* truncation — a capped category quietly
  understates its true hit count. (Read-only observation.)
- **`test_qr.py` embeds a second full QR implementation** (~80 lines: function-module map, mask
  reversal, zigzag scan, format decode) as its oracle. Correct for a zero-dependency repo, but the
  QR spec now lives in two places that must stay in sync.
- **The `conftest`-is-the-only-bootstrap rule is retired.** `pyproject.toml` now sets
  `pythonpath = ["src"]`; the untracked `conftest.py` keeps a redundant `sys.path.insert` and its
  real remaining value is the `SampleBackup` path fixtures.

### E. Firewall, credentials and licensing (flagged, not touched)

- **The plant-identifier firewall holds in every tracked file.** Fixtures use TEST-NET
  (`192.0.2.x` / `198.51.100.x` / `203.0.113.x`), `FakePlant`/`YourPlant`, and the known-safe
  `RB`/`RC` families throughout. The three `cvx_handshake/*.bin` blobs were checked
  programmatically: every `TCP:<ip>` field in all three is a documentation-range placeholder,
  rewritten to the dialed IP at connect time. The git-excluded tests *do* assert on real robot
  names and F-numbers — correctly firewalled, and anything merging them into the public suite has
  to scrub the assertions, not just swap the fixture.
  ⚠️ One gap: `test_cvx_remote.py` checks only that the `TCP:` field parses and can be replaced,
  **not** that its contents are TEST-NET — a future re-capture from live hardware could reintroduce
  a real address into an opaque binary without failing a test.
  > **✅ RESOLVED `f3b8ebd`.** `test_bundled_handshakes_carry_no_real_address` now requires every
  > address in every bundled blob to be an RFC 5737 documentation range — checked at the `TCP:`
  > field *and* anywhere else a dotted quad appears, since that field is not the only place bytes
  > can hide. Passes today (all three are `198.51.100.249`); verified it fails when handed a
  > plant-shaped address. **If a re-capture ever trips this, scrub the blob — do not relax the test.**
- **Two literal credential pairs sit in public source.** `mtxbackup.py` hardcodes the Matrox DA
  camera's vendor-default share credentials (with a comment on their exact casing), and
  `home.js`'s `startLineBackup` names the default camera credentials in a comment. Vendor defaults
  rather than plant secrets, but they are credentials in a public repo.
  > **DECIDED: leave them.** They are published in Matrox's own documentation and burned into
  > every DA camera, so moving them buys no security; a config file would break the
  > offline-single-exe contract; and the casing comment is live-verified field knowledge
  > (`Matrox` authenticates, `MATROX` does not — that comment is why first backups stopped
  > failing). The real gap this surfaced is a **feature**, not hygiene: *a site that rotates the
  > default has no way to supply the new one.* Tracked in ROADMAP.md.
- **`overview.js` (~line 408) names a person** as the source of a UI decision. Not a plant
  identifier, so not a firewall breach, but it is a real name in a public comment.
  > **✅ RESOLVED `b708f87`.** Rationale kept, name gone. Note that "Cody" ×3 and "Jake" ×2
  > remain elsewhere as design attributions — those are the maintainers' own first names in
  > their own repo, and were left deliberately rather than scrubbed silently.
- **Licensing paper trail is thin.** `base.css` calls the bundled Orbitron "SIL OFL" but
  `web/fonts/` holds only the `.ttf` — no `OFL.txt` beside it, and grepping the markdown for
  "OFL"/"Orbitron" returns nothing. The bgfx AGPL-3.0 attribution exists only as the `FX_CREDIT`
  string in `settings_ui.js:47`, not as a license file.
- **`phoneview.py` renders HTML from Python** — a ~85-line `_PAGE` of markup and script, against
  CLAUDE.md's "Python never renders HTML". Defensible (it is served to a phone's browser over the
  LAN, not into the WebView), but it is the one place the rule bends.

### F. Repo shape

- **There was no `docs/` directory before this file.** Root-level markdown (`README`, `CLAUDE`,
  `CHANGELOG`, `ROADMAP`, `CVX_FTP_LAYOUT`) has been the de facto docs home.
  > **Now tracked** (this file and `docs/proposals/`). An untracked doc does not exist on a
  > clone, which defeats the purpose of writing one.
- **Two ignore mechanisms carry different intents.** `.gitignore` holds the public build and
  plant-data rules; `.git/info/exclude` holds 20 test/probe files plus the three sandboxes,
  `build_exe.log` and `robot modelas/`. A fresh clone therefore runs a materially smaller suite
  than this working copy contains.
  > **Narrowed to 17** (`85e147c` un-excluded three). The gap is now measured rather than
  > guessed: a bare clone runs 585 unit tests and 594 with the probes, against 611 / 620 here.
  > **Do not add a tracked `tests/conftest.py`** — the excluded one holds the real
  > `SampleBackup` fixtures, and a tracked file of that name would collide with it. That is why
  > `pythonpath` lives in `pyproject.toml`.
- **`CHANGELOG.md`'s "Unreleased" section is large** (review-your-edits, the 4-pane split, inline
  diff, CV-X simulator workspaces, CV-X self-naming, simulator-folder export) — `main` is well
  ahead of the released v1.4 exe. Version numbering itself is consistent across all three places
  it lives (`pyproject.toml`, `__init__.py`, README badge).
- **`ROADMAP.md` lags the merged state** — it still describes the phone-view and CV-X-photos lanes
  as "landing on the branch" though those PRs are merged and the files are in the tree.
- **Fragile hard-coded counts are a repeated probe pattern:** `ui_batch_probe.py` asserts
  `BV.tabs.length == 16` and exactly 43 `.lib-robot` rows; `ui_sim_export_probe.py` asserts an
  exact settings-row ordering. Any tab or row addition breaks a probe far from the change.
