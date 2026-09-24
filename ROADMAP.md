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
- ✅ **Backup export** — the manage-backups modal's third tab: copy the newest
  N completed backups of picked robots/cameras to a folder/USB stick, laid
  out by a drag-to-reorder template (plant/line/robot/date/time, each
  omittable, plus typed-name custom folders; default `Plant\Line\Robot\Date`).
  Engine-planned foldable preview, collision refusal, an only-the-last-X-days
  window, verified copies or CRC-verified `.zip` archives at a chosen folder
  level, read-only over the library, opt-in `robot.json` identity ride-along.
  The natural next slice if it's wanted: an "import from a stick" counterpart
  (today the receiving side is copy-into-library + rescan, which a full-shape
  export already satisfies).
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
- ✅ **Library overhaul: incremental load + details view** — metadata edits
  (rename, camera link, notes) no longer trigger full rescans; the listing
  serves the cached library instantly and rescans on a background thread;
  cold scans stream robots in as found, favorites first; the home screen
  became a dense details view (aligned columns, sticky sortable header, one
  frosted panel per plant) with linked cameras collapsed behind per-robot
  expanders and a direct link-to-robot row action.
- ~~📋 **Workspace splash screen**~~ — superseded by the library overhaul:
  boot now serves the last-known library instantly and verifies behind it,
  so there is no library wait left for a splash to hide.
- ✅ **Scans as background jobs** — shipped 2026-08-20: the fleet health scan
  and network discover detach on window close instead of cancelling; the
  backup progress strip carries one row per live scan (open re-attaches, ✕
  cancels), detached finishes still save their report, and app close asks
  about running scans. The strip's bulk poll is a light snapshot
  (`list_scan_jobs`, results stripped). `ws_find_programs` (the workspace's
  cross-library program finder) is still a synchronous call — promoting it to
  the same job shape is the natural next slice if plant-scale searches start
  to hurt.
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
- ✅ **Plant-link status indicator** — a statusbar pill that answers "is it me,
  the network, or the device?" without leaving the app: no plant adapter / no
  link / no ip / no gateway / connected, read from this laptop's own adapter,
  gateway and neighbour tables via `iphlpapi` (netlink.py). **The switch is
  never contacted** — no SSH, no SNMP, no management plane. Clicking it drops a
  panel listing the segment, merged with the library, flagging devices that are
  not in it. Passive by default at zero added packets; the explicit "check now"
  sends one ARP per listed address (layer 2, no service touched) and refreshes
  the very table the panel already reads, so there is no second source of truth.
- 📋 **Library-wide content search** — "which robots call PROG_X / use R[57] /
  reference DI[279]" across the whole library, not just the open backup.
- 📋 **Absorb `tools/restyle.py`** — the style-clone kit builder gets UI inside
  the app.
- ❓ **Scheduled backups** — nightly fleet backup reusing the run log / retry /
  complete-marker machinery. The retention half landed for manual use (the
  manage-backups cleanup tab: `library.retention_verdicts` + `_staged`
  staging — moves, never deletes); an unattended nightly run should reuse that
  same engine, and its only power stays "stage a move" — emptying `_staged`
  remains the human's Explorer step.
- 📋 **Camera credentials a site can actually change** — `mtxbackup.py` hardcodes
  the Matrox vendor defaults (`mtxuser` / `Matrox`, both case-sensitive), which
  is correct: they are published, they are burned into every DA camera, and a
  config file would break the offline-single-exe contract. The gap is the plant
  that *rotates* them and then has nowhere to put the new pair. State measured
  2026-08-01 (`docs/subsystems/backup-capture.md` §9): the engine already accepts
  overrides and the batch flow would carry them — set an `ftp user` on a camera
  entry and the shared password prompt rides along — but the **edit modal sends a
  user with no password**, and none of it is documented or discoverable. So this
  is smaller than it looks: finish the modal's credential pair, then say so in
  the UI. Until it lands, a rotated-credential site cannot back its cameras up
  at all.

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
  not the DCS model" labeling per the locked ruling). **New route as of
  2026-08-10:** a CV-X 3D-pick backup carries a *ready-made* FANUC arm mesh —
  `RBT_G_RMD_*.dat` decodes through `cvx_models.raw_facets` with no
  robot-specific code (32,250 facets, an M-20iD/35). Caveat that decides how
  usable it is here: it is ONE fused mesh at its saved pose, not per-link
  parts, so it cannot be posed by joint angles without a split — fine as a
  static body, not a substitute for per-link geometry.
- ✅ **Program points in 3D** — pick a program in the 3D view (toolbar picker,
  or "view in 3d" from the programs tab) and its taught path draws among the
  zones: cartesian points composed through their own UFRAME, joint-recorded
  ones placed by the same pendant-proven forward kinematics the arm poses on,
  and every move that cannot be placed listed with the reason. Needs no
  kinematics for the cartesian half. The slice also landed the viewport's
  first tracked probe (`ui_view3d_probe.py`), which is why 3d-viewer.md §8
  no longer opens with "the viewport renders under no test at all".
- ✅ **Play the path** — pick a program, press play, and the arm walks it
  through the zones. Joint-recorded points pose by their own taught angles
  (exact, no solver); cartesian points go through a damped-least-squares
  solve accepted only when the forward chain reproduces the taught pose.
  Linear and circular moves are walked in substeps along the drawn line so
  the arm follows the path rather than bowing off it, and playback is one
  uniform rule — lerp in joint space between knots — which a joint move
  satisfies exactly. Timing is the programmed feedrate where the listing
  proves it and a stated assumption where it cannot; the viewport says "path
  preview — not a cycle-time simulation" throughout.
- 📋 **Say how much of a program the view could not draw, before you open it**
  — the picker lists a program's taught-point count, but not how many of its
  moves resolve. A listing whose points are all masked, or all behind runtime
  offsets, looks as promising as one that draws perfectly until you pick it.
- 📋 **Cycle time for real** — acceleration and deceleration ramps, CNT
  blending between moves, and the per-model maximum joint rates a percentage
  move is actually a percentage OF. The first two are motion-planner work; the
  third is data no FANUC backup file carries, so it would have to come from a
  table like the kinematics one, with the same validated/unvalidated honesty.
- 📋 **Runtime offsets and INC** — a move carrying `Offset,PR[n]`,
  `Tool_Offset,PR[n]` or `INC` is drawn at its taught point today and labelled
  as such. Composing the register would place them properly, when the register
  is one the backup can actually prove (an initialised PR nothing writes).
- 📋 **Decode the taught CONFIG, and select the IK branch with it** — the
  config string (`N U T, 0, 0, 0`) names the wrist flip, elbow up/down,
  front/back and the J1/J4/J6 turn counts, and nothing in this repo has
  pendant-paired any of it, so today it is carried verbatim and never read.
  Until it is, a cartesian point poses at a **solver-chosen** branch: same
  TCP, possibly a mirrored elbow, and the residual is ~0 so no runtime check
  can catch it. Two things make this cheap when someone takes it. `CURPOS.DG`
  prints joint angles **and** the config string the controller computed for
  them, so every backup carries one free ground-truth pair (`parse_curpos`
  reads the six world floats today and drops the string — a two-line change).
  And with the letters proven, the closed-form **OPW** solution
  (Brandstötter/Angerer/Hofbaur, the one the vendor's own SDK dispatches to)
  enumerates all 8 branches so the taught one can be *chosen* rather than
  labelled. Measured against the shipped table: 121 of 228 chains reduce to
  OPW as written — 167 are 6-joint, 152 of those have a spherical wrist (the
  15 misses are exactly the CRX family, which the vendor also solves
  separately), and 36 more fail only because a side-slung or undersling mount
  rotates the base. So OPW would need a numerical fallback either way, which
  is why the numerical solver goes in first.
- 🐛 **Mesh-renderer regressions (field-reported 2026-08-28; PARKED, circle
  back)** — after the 2026-08-20 perf engine (`28b960f`): occasional missing
  triangles, and wrong in-front/behind stacking, in the camera mesh viewer.
  Ranked suspects, most likely first, each with its fix direction:
  1. **One global cull orientation vs multi-component meshes.** An internal
     void or second shell is *legitimately* wound opposite the outer skin, so
     the directed-edge pairing proof still passes while the single `cullSign`
     (leaned off the max-x vertex) culls that component's FRONT faces —
     missing geometry wherever a void or nested shell shows. Fix: label
     connected components (edge adjacency), orient each independently — or
     refuse to cull any multi-component mesh.
  2. **Perspective-mode approximations at the silhouette.** The per-face
     centroid ray can cull a large triangle that is still partly visible,
     and run batching keys winding on the ortho view-dot, which can disagree
     with true projected winding under perspective — opposite windings in
     one nonzero-rule path cancel to holes. Fix: cull only when all three
     corners face away; key batches on the actual projected 2D winding sign
     (exact in both projections).
  3. **Quantized-depth ties.** The O(n) counting sort holds order to 1/4096
     of the depth range; near-coplanar overlaps that the old exact comparator
     kept stable can now flip frame-to-frame across bucket boundaries
     (stacking shimmer). Fix: more buckets, or an exact polish pass.
  4. **Coarse drag frames** (0.6× store, hairline skipped) reading as
     dropout/sparkle on fine meshes — a tuning knob (`COARSE_SCALE`,
     `COARSE_TRIS`), not a defect; the settle frame is always full quality.
  Kill-switch while parked, if it bites in the field: force `cullOn = false`
  in meshview.js `redraw()` (one line) — forfeits the cull's half, keeps the
  batching/sort/coarse speedups, and suspects 1 and the cull half of 2
  vanish with it.
- 📋 **Program points in 3D** — plot a program's Cartesian positions among
  the zones (compose their UFRAME); joint-rep points can now use the same
  forward kinematics the posed arm runs on.
- 📋 **Rail + mount variants** — the pose validator exposed them: rail
  robots miss by exactly their carriage travel (pure translation, perfect
  orientation) and some mounts by a constant rotation. Both refuse to pose
  today (honest); modeling the rail axis and mount orientation would bring
  them in. Needs the aux-axis direction + mount angle from the backup. The
  carriage *value* is in hand since 2026-09-11 — `E1`/`EXT1` is parsed on
  every taught point and PR (`ext` on the group) — so the missing piece is
  purely the axis direction + mount.
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

- ✅ **The wall feeds every tile, and CV-X has an off switch** (`cam-fair`).
  The per-beat load budget was handed out to a DOM-order prefix, so exactly
  twelve tiles were ever fed however big the wall got — and the starved ones
  were *silent*, not dark, because a tile that is never asked never fails.
  The budget rotates now (never-painted first, then a resuming cursor), the
  near-screen margin shrank so off-screen tiles stop competing, and a tile
  with no picture yet says so. Alongside it, a **CV-X live** switch at the
  right of the library toolbar drops the whole vendor off the wall and hangs
  up its sessions at once, handing back the controllers' single remote slots.
  See §7 of `docs/subsystems/remote-mobile.md` for why three separate test
  gaps let this reach a plant floor.

- ✅ **Both remote bars carry the same options** — reload · open in window ·
  phone · fullscreen · close, on Matrox and CV-X alike. CV-X reload is a
  Python-side hang-up-then-redial under the same session id, and its pop-out
  window boots on a `#cvx=` fragment and *adopts* the live session: the
  controller's single remote slot is never asked for twice.

- ✅ **CV-X cameras in the cam lens — live tiles** (claimed and landed
  2026-08-14, on `cvx-live-tiles` stacked on the stream-flush fix) — the
  multicam wall tiles Keyence controllers beside Matrox by mirroring each
  screen through the shipped remote-desktop bridge, strictly view-only: no
  input path is ever wired to a tile, and `cvx_remote_mouse` refuses a tile
  session outright. One session per controller (that is all the CV-X offers);
  sessions are leased by the visible grid (`cvx_tile_sync` per tick) and
  reaped within `CVX_TILE_TTL` (8 s) whenever the wall is not being watched —
  lens flipped, window hidden, tile scrolled away, an overlay or modal up —
  so the controllers' single remote slots free up for other terminals.
  Clicking a tile *adopts* its live session into the full remote (the pop-out
  pattern), never a second dial. Prerequisite landed first: the MJPEG bridge
  re-sends a settled frame once after a 150 ms idle, because Chromium's
  multipart parser only paints part N when part N+1's boundary arrives —
  without that, a tile of a quiet camera would simply stay blank (live-proven:
  a real CV-X pushes exactly one frame at connect, then silence).

  ✅ **Corrected on the plant floor 2026-08-18 — a tile polls, it does not
  stream.** Eight cameras on one line and every CV-X tile read "no image — not
  answering" while the same camera opened fine on click. Nothing was wrong with
  the sessions (python logged a clean handshake for all eight): a
  multipart response never completes, every tile streamed from the one
  `127.0.0.1:PORT` origin, and the browser caps connections per origin at six —
  so the seventh tile onward never connected, got no `load` **and no `error`**,
  and the 8 s honesty timer wrote "not answering" over a camera that was
  answering. A successful dial also parked `_camDue` at `Infinity`, so a tile
  that lost the race was never re-kicked and the wall latched dark tile by tile.
  Tiles now GET a finite still (`/cvxshot/<sid>`) on the grid's existing 2 s
  beat — the socket comes straight back, so the wall scales past six — and the
  stream stays for the overlay, which is one viewer. The lesson worth keeping:
  every CV-X test used ONE camera, and this failure mode is invisible at one and
  total at eight; `tests/ui_camwall_probe.py` is now the plural case.

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

- 🔨 **CV-X photos** — the photos tab now covers Keyence as well as Matrox.
  A CV-X stores every scene twice, a grayscale photo and a height map of the
  same moment, so the two pair into one record and the hero crossfades between
  them on a slider. The height file is *not* a photo: 24-bpp BGR packing a
  15-bit range value (`H = (G<<7)|(R<<4)|B`, all-zero = no data), read off real
  files and pinned by tests — see `parsers/cvx_image.py`, which also records
  what could NOT be proved (a true height of 0 is indistinguishable from no
  data) and the dead end (the `.tbd` blobs carry no *images* — though the
  2026-08-07 decode showed they DO carry zlib-wrapped binary-STL geometry;
  see the CV-X 3D models bullet below). Rendered to PNG through
  the phone view's stdlib encoder, decimated during the decode so a 12 MB
  master costs ~0.4 s and the stack stays locked. What there is to show is
  mostly taught masters; timestamped triggers only exist when a tech turned
  image logging on. Landing on the `cvx-photos` branch.

- ✅ **A Matrox backup carries a run of photos, and re-runs stop cloning
  folders** (landed on `mtx-photo-history`) — the pull took the newest
  `SavedImages/<date>/` folder, which on a real camera is usually one
  inspection: a 367-file snapshot with a single photo in it. It now takes the
  newest N photos (`mtx_photos`, default 25, a slider in ⚙ → preferences),
  walking date folders newest-first; the newest day comes whole and older
  photos leave the png behind (same 1920×1200 frame as the jpg beside it, ten
  times the bytes — measured). And a re-run whose `da/` tree is unchanged now
  folds its new photos into the snapshot they match instead of stacking a
  ~400-file near-twin, which is the ONE ruled exception to "nothing writes
  into a backup folder" (CLAUDE.md · backup-capture.md §5 inv. 7): adds only,
  never a partial, never a rewrite, and only after this pull is already a
  complete snapshot in its own right. Verified against two real pulls of one
  camera two minutes apart (425 non-photo files identical). Still owed: a run
  against a live camera — everything so far is against real *pulled* trees.
- ✅ **CV-X 3D models: viewer + STL extract** (landed on `cvx-camera-tabs`) —
  a camera backup's `TDC_L`/`WSM_L` blobs decode to the registered part CAD
  and workspace scans (zlib-wrapped binary-STL facets, `parsers/cvx_models.py`);
  the camera's 3d view tab renders them in a canvas-2d viewer on `proj3d`
  math and exports real `.stl` files; overview gains the crossfade hero +
  program/robot/calibration cards. The `HND_L` gripper/EOAT mesh decoded too
  (2026-08-10) and renders and extracts like the part CAD — it is the same
  facet geometry, uncompressed, in 48-byte records at an offset 2 mod 4, which
  is why an earlier pass read it as noise — and the `RBT_G_RMD` arm mesh came
  free with it, the same 48-byte records (a whole M-20iD/35, 32,250 facets).
  `TDM_L` matching templates stay an honest info card; that encoding is still
  unreversed. WebGL still parked; canvas 2d works under software rendering.

- ✅ **CV-X inspection logic: the scripts and the names decode** (landed on
  `cvx-camera-tabs`, 2026-08-11) — `inspect.dat` is an `ST` container that is
  ~98 % zlib blocks, stored as a working + recovery copy; inside, two regions
  are self-describing text. The **calculation scripts** a technician wrote come
  out as plain ASCII in the CV-X's own expression grammar (`@local`, `ANSn`,
  `IF/ELSEIF…THEN/ENDIF`, `Tnnn.RSLT.<MNEMONIC>[i]:MS`), and the **tool names**
  come out of a length-prefixed language table (`(u32 lang, u32 len, bytes)`,
  ascending indices, slot 1 = English). Read off real cameras plus a controlled
  experiment in the vendor's simulator; parser `parsers/cvx_program.py`, surface
  the camera **logic** tab.
  **What this does NOT solve, so nobody assumes it:** (1) **per-tool mapping** —
  no tool number or type code has been found beside a name record, and no owner
  field beside a script, so names are a list and never "tool 5"; (2) a
  technician's names and the vendor's built-in tool-type vocabulary are
  indistinguishable in-file, so both are shown together; (3) the **parameter
  slots** (float64 at an offset 6 mod 8 with a ~1e12 "unset" sentinel) are
  unmapped — exactly one was located by experiment, so a camera's *settings*
  remain unread and nothing numeric is surfaced. The route for (3) is the same
  experiment repeated one setting at a time; see `docs/subsystems/parsing.md`
  §8 items 10–11.

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

- 🔨 **Backup-folder import** (claimed 2026-08-14, branch `import-backups`) —
  drag an existing backup folder (one robot or a whole slice) onto
  `+ add robot`, pick plant/line, and the app copies it into the library tree
  for the normal scan to adopt. The drop-side sibling of the LibraryImporter
  lane below, not its absorption: this imports *backups that exist*, that
  seeds *robots that don't have backups yet*.
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
