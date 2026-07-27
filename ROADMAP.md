# BackupViewer — roadmap

Where the project is headed, so parallel work doesn't collide. If you want to
pick something up, say so first (issue, message, whatever works) — claiming a
lane beats discovering two half-built versions of it. Ground rules for *how*
to build any of this live in [CLAUDE.md](CLAUDE.md); what already shipped is
in [CHANGELOG.md](CHANGELOG.md).

Legend: ✅ shipped · 🔨 being built · 📋 decided, not started · ❓ open question

## Recently landed

- ✅ **1.0** — library-first shell, history/time-travel compare, fleet health
  scan, backup integrity (complete-marker), manage-backups tooling.
- ✅ **1.1** — the 3D View tab: DCS cartesian zones drawn to scale, free
  turntable orbit + viewport cube, ortho/perspective, per-zone show/hide with
  pendant-style detail.
- ✅ **LibraryImporter 0.1** — standalone hand-out seeder (robots.json → library
  skeleton), built with a parser seam so it can absorb into the app later.

## 1.x train — small, mostly independent slices

Each of these is deliberately scoped to land on its own. Good places to start.

- 📋 **Report export** — CSV per table, self-contained HTML report, print-to-PDF.
- ✅ **Browser-style tabs** — several backups open at once; tear a tab off
  downward (or out of the window) to float it. Shipped with the per-session
  refactor (`_sessions` dict); the compare `side` parameter is
  trailing-positional — see CLAUDE.md before touching endpoint signatures.
- 📋 **Group pop-out windows** — several backups in ONE external window, and a
  way to send a floating backup back. Today a pop-out holds exactly one, and
  once it is out there is nothing to grab. WebView2 has no cross-window DOM
  drag, so this cannot be a drag: it needs a `#sessionbar` inside the pop-out
  plus an endpoint that reassigns a session's owner window, driven by a
  "move to → main window / <other window> / new window" menu on each tab, and
  a push so both windows repaint. Decided 2026-07-25 to ship the tear-off
  gesture first and leave this whole shape for its own slice.
- 📋 **Workspace splash screen** — start the library load while the splash is
  up, so the wait buys something instead of costing it.
- 📋 **More scan checks** — simulated-IO-left-on, general override < 100%,
  alarm-frequency summary, controller clock drift, uninitialized PRs
  referenced by programs.
- 📋 **Golden-robot compare** — pick a reference robot; the scan flags every
  deviation from it across a line.
- 📋 **First-run tips + in-app help** — bundled docs (plant PCs are offline),
  empty-state guidance.
- 📋 **UI scaling audit + XXL preset** — raise the current 24px/160% caps
  without layouts collapsing; probe-gated at the extremes.
- ✅ **Auto-update check** — shipped: the packaged exe pings GitHub releases
  once on boot (toast + statusbar pill, fully offline-tolerant), the about
  box checks manually anywhere; see CHANGELOG.
- 📋 **Library-wide content search** — "which robots call PROG_X / use R[57] /
  reference DI[279]" across the whole library, not just the open backup.
- 📋 **Absorb `tools/restyle.py`** — the style-clone kit builder gets UI inside
  the app.
- ❓ **Scheduled backups + retention** — nightly fleet backup reusing the run
  log / retry / complete-marker machinery; needs a keep-last-N + monthly
  retention policy before it's safe to leave running.

## 3D View follow-ups

- ✅ **Kinematics + posed arm** — the vendor's own `.def` kinematics imported
  once from a simulator install into a local registry (exe ships zero vendor
  data), the arm posed from the backup's own `CURPOS.DG` snapshot (or by
  hand), DCS user models drawn at their true frames. Every pose
  self-verifies against the controller's printed TCP; "-IF" flange
  adapters are measured per robot from the backup. Unmatched types
  honestly stay un-posed.
- 🔨 **Robot meshes in the viewport** — the skeleton wants a body: Roboguide
  `.rcf`/`.hsf` mesh crack, or `.rmd`/STL/OBJ import (the `.rmd` format is
  fully reversed), then the capsule fitter for arm bubbles ("visual approx —
  not the DCS model" labeling per the locked ruling).
- 📋 **Program points in 3D** — plot a program's Cartesian positions among
  the zones (compose their UFRAME); joint-rep points can now use the same
  forward kinematics the posed arm runs on.
- 📋 **Rail + mount variants** — the pose validator exposed them: rail
  robots miss by exactly their carriage travel (pure translation, perfect
  orientation) and some mounts by a constant rotation. Both refuse to pose
  today (honest); modeling the rail axis and mount orientation would bring
  them in. Needs the aux-axis direction + mount angle from the backup.
- 📋 **Compare overlay** — ghost the comparison backup's zones into the
  viewport (the "what changed in DCS" killer view).
- ✅ **Lines-mode zones** — `$MODE=3` ↔ Restricted zone(Lines) and `$MODE=0`
  ↔ Working zone (keep-in) ground-truthed against real controllers; polygon
  zones draw with their true vertex count. Only `$MODE=2` remains unmapped
  (never seen on a real controller — still shows `?`).
- ✅ **User models + target refs** — `$DCSS_MODEL` (EOAT element geometry)
  and each zone's `$MODEL_NUM` slots parsed and resolved; data-only in the
  panel until kinematics can place link-attached shapes.
- 📋 **Newer-vocabulary verify reports** — the "Working/Restricted zone"
  pendant generation prints its Lines vertex table in a layout the report
  parser shows raw (correct but unstyled); teach the pos-table parser that
  shape when a backup needs it.

## Cameras — Keyence / Matrox (owned lane, in progress elsewhere)

Attach vision devices to robot entries; open a robot, see its cameras;
one click backs up the robot + all its cameras together.

- 🔨 **Phone view** — QR handoff to a phone browser mirroring the Matrox
  window (the app window's client area, grabbed live via GDI + stdlib PNG so
  it follows moves/resizes; hand-rolled stdlib QR; camera-direct HMI relay
  kept as the API variant). Landing on the `phone-view` branch. Design
  history, so nobody re-treads it: a snip-a-rectangle picker was built first
  and cut — WebView2 can't do transparent or capture-excluded windows on
  Win11 (so no live hollow frame), and even the fullscreen-screenshot picker
  was fiddlier than the plant wants. Mirroring the whole window is what
  landed: one click, no placing. Since the `remote-bar-parity` lane the
  button lives in the **top bar**, so it reaches any screen, and mirrors the
  window it was pressed in (`viewfinder_start {window}` — a key naming one of
  our windows, never a raw title).

- ✅ **Both remote bars carry the same options** — reload · open in window ·
  phone · fullscreen · close, on Matrox and CV-X alike. CV-X reload is a
  Python-side hang-up-then-redial under the same session id, and its pop-out
  window boots on a `#cvx=` fragment and *adopts* the live session: the
  controller's single remote slot is never asked for twice.

- ✅ **A CV-X backup opens in the simulator** — the pull lands under `SD1/`
  with the simulator's `workspace.xml` beside it, so the camera folder in a
  backup *is* a workspace (format read off 52 real files, reproduced
  byte-for-byte; see `CVX_FTP_LAYOUT.md`).
- ✅ **Load cameras into the simulator** — the simulator reads ONE flat base
  path, so ⚙ → preferences gained a `simulator folder` row (copy-path button +
  a camera picker) that copies chosen cameras' latest workspaces side by side
  into it. On demand, not automatic: ~100 MB each. Naming follows the shop's
  station convention and refuses to let two stations claim one folder.
- ✅ **CV-X self-naming** — a camera discovered as a bare IP renames itself from
  the names of its inspection programs (`parsers/cvx_inspect.py`), through the
  same `library.teach_camera_name` Matrox uses. 85% of real cameras yield a
  station tag; the rest yield the part name the tech typed.
- ✅ **The simulator folder will not eat a workspace we did not write** — the
  export keeps a ledger (`.backupviewer-exports.json`, at the base path, never
  inside a workspace) of the folders it created. Replacing one of those is
  routine; anything else raises `ForeignWorkspace`, comes back from `sim_export`
  in `blocked` rather than as an error, and is only destroyed after an explicit
  "replace them". A missing or corrupt ledger fails toward asking.
- 📋 **Export OLD pre-`SD1/` backups as workspaces** — they have no
  `workspace.xml`, so they can't be offered in the picker. Must COPY into the
  flat folder (restructuring a taken backup is off the table); the export
  primitive already exists, it needs the wrapper that synthesises the missing
  manifest from the backup's recorded host.
- 📋 **Pull `/SD1/cv-x/workspace/`** — the camera stores simulator workspaces
  on its own SD card and our backup currently misses them. Cheap: one more
  target in `keyence_enumerate`, gated like `box/` since size is unknown.

- 🔨 **Discovery** — agreed direction: probe the DesignAssistant web portal
  (:80/:443) and EtherNet/IP ListIdentity (UDP 44818, Matrox vendor ID) for
  the newer Iris GTX — the old FTP/SMB port gates only find Keyence CV-X and
  the older GTR.
- 🔨 **GTX backup transport** — SSH/SFTP (or a DA HTTP export), not SMB; SMB
  stays as the GTR fallback. Blocked on a live credentials/endpoint spike.
- 📋 **Data model** — cameras link to a robot via sidecar *config* (a parent
  id), never by folder identity; group backup fans out to per-device jobs and
  reuses the complete-marker / run-log / retry machinery per device.
- ❓ **Shared cameras** — can one camera serve two robots? Decides whether the
  link is single-parent or a list.

## DCDL importer

- 📋 Absorb LibraryImporter into the viewer as an import wizard (the parser
  seam in `libraryimporter/core.py` exists for this).
- 📋 Parse a raw DCDL (site-wide device/IP list) directly: generate the robot
  *and camera* lists from it. Re-import is a **suggest-only diff** (new /
  retired / changed-IP) — never destructive.
- ❓ Needs a sample DCDL to pin the file format.

## 2.0 — editing (the headline)

In progress (claimed 2026-07-23, first slices building on main):

- ✅ **Program (.LS) editor v1** — view↔edit toggle on the program detail;
  edit mode is a pendant-like structured editor (auto line numbers in a
  gutter, live TP syntax colors, no `;`/scaffolding — you type instructions
  only), details toggle reveals editable attributes (owner/comment/protect)
  and point data (masked `********` points are uninitialized, typing a value
  initializes them). Save exports edited `.LS` to a user-picked flat folder —
  never the backup. Engine: `parsers/ls_edit.py` (split/renumber/re-emit,
  byte-exact for untouched lines; format rules measured over 6478 real
  programs / 378k lines, round-trip fuzz in `tests/test_ls_edit.py`).
- ✅ **Multi-robot edit workspace** (`#edit`) — the editor moved off the
  program detail and onto its own shell screen, because a working set spans
  robots while the tab strip is per-backup. Rail (working set · find/replace) │
  panes │ navigator. Programs are read and exported through path-addressed
  `ws_*` endpoints so the workspace never consumes a session; export writes one
  folder per robot and never into a backup. Find/replace matches on IDENTITY
  (`R[21]` finds `R[21:SERVO GUN WORK]`), which is the thing VS Code
  structurally cannot do.
- ✅ **Editor ergonomics** — split view is derived from where programs are open
  (drag onto the right quarter to open it, close the last tab to fold it away),
  the working set is multi-selectable by click/ctrl/shift with Delete, and the
  workspace is reachable from the topbar anvil or ctrl+E anywhere.
- ✅ **Loading gate CLOSED** (field-verified, hundreds of loads): the
  controller's `.LS` load is liberal — it rejects with line + column on a
  syntax error, `LINE_COUNT` need not be updated, and lines need not even be
  renumbered in order. Our renumbering is a convenience, not a correctness
  requirement.
- ❓ **Hardware gate (remaining):** whether loading an `.LS` with edited
  comments OVERWRITES the controller's comment table. If yes it is both the
  offline path for fleet-wide register/IO renaming and a hazard (loading an old
  program silently reverts renamed comments) — the hazard half is already
  detectable offline, so it is a candidate scan check either way.
- ✅ **The two diff views** — review-your-edits (original vs edited for every
  kind of change: body, attributes, points, renames; the compare engine
  pointed at pristine vs buffer, exactly what the export writes) and the live
  pane-vs-pane diff (split view only, recomputes while typing). Pane-vs-pane
  classifies on IDENTITY: ref comments and the pendant's IO-status display
  are save-time state, so `DI[10:OFF:Comment]` vs `DI[10:Comment]` reads
  "display-only", never "changed". One shared renderer (`BV.pdiffView`)
  serves both plus the #pdiff tab.
- 📋 Next in this lane: insert-CALL picker (pick a real program/macro from
  the backup), then validation/autocomplete against the backup's own IO and
  register tables.

Decided principles (these are settled — build against them):

- 📋 **Never soil the backup.** Backups stay read-only evidence; edits live in
  a sibling workspace (overlay). The review-your-edits screen is the existing
  compare engine pointed at original vs overlay.
- 📋 **Apply paths, not binaries.** Programs export as edited `.LS` (text).
  Register/PR/frame values export as a generated one-shot APPLY program of
  literal assignments — reviewable on the pendant, version-proof. Comments
  (register names, IO) push live via the controller's web comment hook when
  on the network. No synthesizing `.TP`/`.SV`/`.VR` binaries.
- 📋 **USB-export-first.** Deploy = a named folder on a USB stick with a
  manifest + step-by-step pendant checklist. Direct FTP write-back comes
  later as a separately gated, human-in-the-loop tier — many sites prohibit
  it, and it must never be the default.
- 📋 **DCS is editable, same as other config** (decision 2026-07-17, reversing
  an earlier read/diff-only stance). Integrators author DCS themselves, so
  edit-and-preview is a real need — and the controller's own apply gauntlet
  (passcode → on-pendant review of the exact changes → OK → power cycle →
  signature re-verification) is an un-bypassable human safety gate that an
  exported file cannot skip. So DCS rides the same apply-path + honesty rails
  as every other edit: the export is an inert proposal, always shown
  **un-applied and un-signed**, never as verified. Bonus — getting the numbers
  right in the tool first means fewer trips through that gauntlet.
- ❓ **DCS load format** is the real gate: what file the controller accepts
  (ASCII sysvar vs binary `.SV`, via which DCS import path). Resolve by field
  knowledge or a Roboguide spike before building.
- ❓ Open spikes: ASCII-upload option coverage on real fleets, the web comment
  hook's availability per controller generation, macro-table writability
  inventory, edits-workspace schema.

## Parking lot (real, but not next)

Live view (poll a robot over FTP without taking a backup) · multi-vendor
parsing (KUKA/Kawasaki — formats share almost nothing) · plotting a second
robot's zones in one viewport · anything requiring WebGL (SVG stays the
floor because rescue-mode PCs render in software).
