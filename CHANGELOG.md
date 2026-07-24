# Changelog

## Unreleased
- **Phone view firewall helper.** If a phone reaches the laptop but the page
  times out with *"server stopped responding,"* that's the Windows Firewall
  dropping the port on that network profile (a rule scoped to Public while
  the phone's hotspot is Private is the classic case). The phone-view popup
  now has a **?** in the corner that explains it and offers the exact
  one-time fix: **copy command**, or **add the rule (admin)** — one click
  that opens an elevated prompt and installs an inbound-allow rule for the
  phone-view port range on every network profile. It reports back whether
  the rule is already in place.
- **Phone view: mirror the Matrox window to your phone.** Press 📱 (MTX
  remote bar, camera cards) and a QR appears immediately — scan it and your
  phone shows a black page mirroring the Matrox window (the app window, with
  the remote overlay up), live. No box to draw, no extra window, nothing to
  place: whatever the window shows is what the phone shows, and the capture
  follows it if you move or resize it. The second pair of eyes for focus
  work at the lens: one person's at the camera, the phone is the screen.
  Scan, hit **close (keeps sharing)** so the camera fills the window again,
  and walk to the lens. View-only — the phone sends nothing back. The phone
  just needs to reach the PC (same wifi, or the Windows mobile hotspot — the
  modal ranks the hotspot address first with a chip per adapter). Off by
  default, token-gated; the share stops with the app, the stop button, or on
  its own when it ages out forgotten. A camera-direct relay of the MTX HMI
  frame ships alongside as the API-level variant. The QR encoder is
  hand-rolled stdlib (the stack stays locked), ground-truthed against an
  independent decoder across every version and mask it emits; the window
  grab is ctypes GDI + a hand-rolled PNG writer, so the exe gains zero
  dependencies.
- **Dialogs beat overlays.** Modals (and menus above them) now stack above
  the remote-operation overlay, so the phone-view QR can open from inside
  the MTX remote without vanishing behind it.
- **CV-X remote: full mouse.** The live Keyence remote now forwards the
  scroll wheel (zoom), the middle button, real drags (pan — the controller
  only pans on its dedicated drag events, so dragging used to snap at
  release) and a reliable right-click (context menu). Event ids
  ground-truthed against Keyence's own Vapi.Net.dll, events resequenced so
  they reach the controller in gesture order, and hand jitter during a
  click no longer reads as a drag.
- **Browser-style backup tabs.** Every opened robot or camera lands on a tab
  in the topbar (the content tabs drop to a slim second row): open several
  backups at once, click to switch — every tab restores exactly where you
  left it, scroll, sort, folds and its own compare pairing included — drag
  to rearrange, ✕/middle-click to close, and re-opening a robot focuses its
  existing tab instead of rebuilding it. Backed by a real per-session
  registry (each backup's parse caches stay warm and independent; a hard
  cap of six refuses honestly rather than quietly eating memory), with the
  compare `side` parameter kept trailing-positional per the house rule.
- **Pop-out windows.** Drag a tab out of the app (or right-click → pop out)
  and that backup opens in its own OS window — pinned to the one backup, no
  library or tabs of other robots, closing it really closes the backup.
  The main window closing takes its pop-outs with it.
- **Frosted glass on a slider.** The theme window's background block gains a
  **frost** slider: content surfaces go translucent so the animated
  backgrounds glow through, and the sticky library head / overview hero /
  modals get real backdrop blur (rows stay legible scrolling beneath). At 0
  the app renders exactly as the solid original. The library head no longer
  paints an opaque slab over the background effects.
- **Theme window layout.** The background block (effect + intensity / size /
  frost) tops the **themes** tab with the color packs below it; text size
  and chrome scale became live sliders on customize.
- **The library remembers its folds.** Plant/line expand-collapse state and
  the favorites strip survive closing the app, per lens (backup and
  multi-cam remember separately).
- **Deep backups read fully again.** The viewer's file index now walks a
  backup through the same `\\?\` extended-length form the backup writer has
  used since v0.99h. Before this, a tree whose paths passed Windows'
  260-char MAX_PATH (a deep library root plus a Matrox SavedImages filename
  is enough) would *back up* fine but *view* empty — the walk's stat failed
  silently on machines without the OS long-path policy, so every photo (and
  the deepest da/ files) quietly dropped out of the index and the photos tab
  swore there was nothing to show. The photos were on disk the whole time.
- **The 🎨 theme window.** A new topbar button (and `t`) opens a two-tab
  window that owns everything about how the app looks: **themes** is the
  familiar category accordion (hover previews, j/k/enter, custom-theme
  edit/delete), **customize** collects the appearance and text & scale rows
  that used to crowd ⚙ settings, two new zero-byte system font choices
  (sans, serif) beside mono and ROG, and the background effect with new
  **intensity** and **size** sliders that tune the animation live. The ⚙
  modal keeps app behavior only: 3d view and the library folder.
- **Animated backgrounds.** The theme window's background section offers
  twelve optional ambient effects that draw behind the app chrome — five
  house styles (gradient drift, aurora, particles, starfield, waves) and
  seven inspired by PewDiePie's Odysseus workspace (synapse, rain,
  constellations, flow field, petals, sparkles, embers; AGPL-3.0 source,
  combined per GPLv3 §13 and credited in the window). Every effect tints
  itself from the live theme's accent, so all 28 themes keep their
  character. Off by default and deliberately cheap: one canvas at capped
  DPR, spawn caps, paused whenever the window is hidden, and a single
  static frame under prefers-reduced-motion or when requestAnimationFrame
  is missing (the probe environment). The choice persists like every other
  pref.

- **Release check.** The app can now notice a newer release on GitHub: the
  packaged exe pings the public releases feed once on boot (source runs and
  probes never phone home) and shows a quiet toast plus a statusbar pill;
  the about box grows a **check for updates** button that works anywhere
  and answers honestly — up to date / newer on github (with an
  open-the-release-page button) / this build is ahead of the newest
  release / no releases yet / couldn't reach github. One 4-second attempt,
  no retries: offline is a first-class outcome, not an error. A found
  version can be skipped (silences the boot reminder for that one) and ⚙
  settings gains a check-on-startup switch.

## v1.3 — the camera wall + six sharper scans
- **Six new fleet-scan checks.** Two catch hand-edits left in programs:
  **remarked positions** (motion lines commented out with `//` — the robot is
  skipping taught points; red flag) and **remarked logic** (remarked CALLs /
  IO / logic — reported quietly, since some remarks are deliberate fleet
  standards). Two catch positions that will fault the moment they run:
  **untaught positions** (a motion line references a P[n] the program records
  no data for — INTP-311 waiting to happen; circular-move continuation lines
  included) and **uninitialized PRs in use** (programs read position
  registers POSREG.VA lists as uninitialized — demoted to info when another
  program writes that PR, since it may be set at runtime; indirect `P[R[..]]`
  / `PR[R[..]]` references are counted and disclosed, never guessed at). Two
  read controller config: **general override < 100%** ($MCR.$GENOVERRIDE
  left turned down — the robot runs slow until someone notices) and
  **controller clock drift**, the first check with its own dial: enter the
  drift you'll tolerate (30s · 2m · 5m) right on the picker row, and the
  check compares the controller's own stamp (BACKDATE.DT, seconds; DG heads
  as a minute-resolution fallback) against the moment the backup was
  written — off clocks scramble alarm timelines across a line, and a
  decades-off stamp usually means a dead RTC battery.
- **CV-X remote view: the patchy image glitching is fixed.** The live
  mirror was appending every video message body into the frame whole, so
  each chunk's 40-byte protocol sub-header landed inside the JPEG's
  entropy-coded scan data — where every stray byte decodes as garbage until
  the next restart marker. Frames are now assembled from exactly the
  video-data bytes (sub-headers stripped, control messages excluded) and
  come out byte-identical to what the controller sent.
- **Multi-cam: watch the line's cameras live from the home screen.** A
  backup ↔ multi-cam toggle in the library head re-renders the same
  plant/line folders as a grid of live Matrox tiles — each shows the
  camera's current HMI frame, refreshed only while actually on screen:
  folded lines, tiles scrolled away, a hidden window, and any open modal or
  remote session don't fetch, new loads are capped per beat, and a tile
  waits for its last load to finish before asking again (a slow camera gets
  to answer instead of being aborted mid-frame). Clicking one goes straight
  into remote operation (the MTX web-UI overlay). CV-X cameras aren't
  tiled — they have no live frame to show; their screen mirror stays on the
  robot's photos tab. A camera that stops answering — or never answers —
  says so on its tile, decays to a slow retry instead of hammering a dead
  IP, and comes back on its own; the lens choice persists across launches.
- **Favorite stars.** Star a robot and it pins — nested cameras in tow — into
  a ★ favorites strip above the plant tree, as full rows (checkbox included,
  so star-select-backup works straight from the strip). Toggling is instant;
  in the multi-cam lens a starred robot's cameras lead their line. The star
  is a per-machine overlay flag (like hide): it survives rescans and never
  touches robot.json, so a star click can't trigger a rescan.
- **Multi-cam lens fix batch.** "Manage backups" moved in with the library
  actions, so it stays reachable from the multi-cam lens (it was hidden with
  the selection row — auto-link cameras lives inside it). Each lens now keeps
  its own scroll position, so flipping backup → multi-cam → backup lands
  exactly where you left the tree instead of thousands of rows off. The cam
  filter matches the "↳ robot" name printed on the tiles (the name a tech
  actually knows) and its no-match note says cameras, not robots. An empty
  grid distinguishes "no matrox cameras yet" from "N are hidden — show
  hidden", and the show-hidden count in cam mode counts hidden cameras, not
  hidden robots. Cam lines start folded (plants open, counts on the heads):
  the first flip shows where the cameras are without fetching every line's
  frames at once. Tiles are keyboard-reachable (Tab / Enter / Space, Esc
  drops focus), and a stray "undefined/0" in the filter counter is fixed.
- **Library quality-of-life.** A real multi-line notes
  editor, inline on the row (double-click to type where you are); edit moved
  into the row's ⋯ menu, which also opens on right-click at the mouse;
  auto-link cameras lives in manage backups now; the linked-robot picker is
  the library tree, not a 2400-name dropdown; and only the rows on screen
  are laid out, so a plant-scale library stays snappy while you type.

## v1.2 — the big merge
- **Cameras self-name from their first backup and auto-link to their robot.**
  A camera the scan couldn't name live (no saved images yet, a flaky read)
  lands in the library as its bare IP; now, the moment a backup completes, the
  camera reads its real name + model out of the snapshot it just pulled (the
  newest saved-image sidecar — the camera twin of a robot naming itself from
  `SUMMARY.DG`), renames its folder to match — the folder is identity, so a
  name the folder didn't carry would have been reverted by the very next
  library scan — keeps the old IP name as an alias so nothing recorded under
  it is orphaned, and auto-linking runs to seat it under its robot. A name someone
  typed by hand is never overwritten. Auto-link also gained a **same-name
  fallback**: a robot and camera(s) sharing one name across device types link
  even when the station+robot key can't parse the name — with the same
  ambiguity guards (same-cell tiebreak, or left for manual linking).
- **Matrox camera backups now take on the first try.** The camera password
  baked into the app was the wrong case — `MATROX` where the camera wants
  `Matrox` — and the camera's Linux Samba compares passwords case-sensitively,
  so every programmatic login was refused (`WinError 86` / `STATUS_LOGON_FAILURE`).
  A first backup only ever succeeded when a tech had already opened `\\<ip>` in
  Explorer by hand (the app rode that session), which is exactly why "the first
  backup always fails, the retry works" — and why it looked like "SMB only works
  interactively." Corrected the default; the SMB login now authenticates on its
  own, no manual Explorer step. Live-verified against two cameras.
- **A camera backup that dies midway no longer masquerades as complete.** Both
  camera takers (Matrox and Keyence) now write their `backup.json` marker
  `complete:false` the instant the folder is created and flip it true only after
  every file lands — the same crash-safety the FANUC backup has always had. A
  pull cut short by a crash, a cancel, or a yanked cable is demoted on the next
  scan instead of being adopted as the newest "good" backup.
- **Discovery stays off machines that aren't cameras.** A subnet scan now only
  attempts the Matrox share login on hosts the EtherNet/IP identity broadcast
  already named as cameras — it no longer tries the camera credentials against
  every PC, HMI, or file server that happens to have file-sharing open. The
  camera password is also cleared from Windows the moment a backup finishes,
  never left staged.
- **A linked camera's photos refresh after a new backup.** The robot's Cameras
  tab keyed its cached photo set to a path that never changes between backups,
  so it could keep showing the previous pull's images until the app restarted;
  it now refreshes whenever a new snapshot lands.
- **The live camera views clean up after themselves.** Closing a CV-X remote
  mid-connect no longer strands the controller's single remote session, and a
  slow-loading Matrox page can no longer leave its window stuck open.
- **The camera line and the viewer line are one app again.** Everything from
  v0.99a–v0.99q below (Matrox + Keyence CV-X backup, the photos tab, both live
  remotes, camera↔robot linking) merged onto v1.1's 3d view, fleet health scan,
  durable backup log and LibraryImporter. The posed 3d robot (detailed under
  *the posed 3D robot* below) is first versioned in this release too.
- **Camera backups join the durable run log.** A bulk selection that mixes
  robots and cameras groups as one run — "last run" and **retry failed** cover
  cameras too.
- **Linked cameras nest in the shared library tree** (home and the compare
  picker both), and shift+click ranges follow the nested order the screen
  actually shows.
- **The CV-X remote's replayed channel-open now advertises the address of the
  camera actually being dialed** (was: the capture-time address, verbatim).
- **The discover modal's select-all follows the robot/camera filter** — it
  covers only the visible rows, with the same tri-state checklist as every
  other list.

### also in v1.2 — the posed 3D robot
- **The robot poses in the 3d view.** Import FANUC's own kinematics once
  from a Roboguide install ("import…" in the 3d view's robot panel → the
  `Robot Library` folder; ~260 types, stored locally — the app still ships
  zero FANUC data) and the arm appears as a to-scale stick-figure skeleton,
  posed exactly as the robot stood when the backup ran (`CURPOS.DG`), with
  every DCS user-model sphere and capsule riding its true frame — your EOAT
  bubbles at the flange, among the fences they're checked against. Joint
  fields in the panel repose the arm live; "reset pose" returns to the
  backup's snapshot.
- **Every FANUC robot type is built in — robots pose with zero setup.**
  The full 228-type kinematics table ships inside the app as its own
  dimension-sheet data (~160 KB). Types verified against real controllers'
  position reports carry their validation record (36 robots, all
  ≤0.23 mm); the rest are labeled "not yet validated against a
  controller" — and any backup with a position report still self-verifies
  at runtime regardless. The Roboguide import remains for future types
  and overrides a built-in.
- **The arm has a body.** Capsule limbs sized from the robot's reach,
  tapering to the wrist — a deliberate schematic (not the DCS robot model,
  not a mesh), so the EOAT bubbles read against a robot instead of a wire.
- **No more over-the-top flip.** Orbit elevation now stops exactly at the
  poles: dragging past straight-down used to carry the camera over the top
  and invert the world's screen-vertical — seamlessly, and right at the
  pole the compass cube is face-on so nothing warned you. Top and bottom
  views stay exact; the portal is gone.
- **The pose is verified against the controller's own numbers, per robot.**
  Every backup with a position report self-checks: forward kinematics
  through the taught tool must land on the controller's printed world TCP.
  Plain robots verify to ~0.1 mm; dress-package variants ("-IF") carry a
  real flange adapter the base model lacks — the app *measures* it from the
  backup itself (+23.0 mm on R-2000iC/210F-IF and R-1000iA/100F-IF,
  +10.07 mm on M-900iB/280L-IF across the fleet) and labels it. A backup
  that contradicts the imported kinematics refuses to pose the arm and
  says why. Types with no def imported (or missing from the library, like
  ARC Mate 120iD/35) stay honestly un-posed — never a borrowed arm.
- **DCS user models parsed from the backup** (`$DCSS_MODEL` in `DCSPOS.VA`):
  every EOAT/gripper element with its shape, radius, positions and link —
  cross-checked digit-for-digit against the pendant's verify report. The 3d
  view's "user models" rows now carry the full geometry (`element 1 ·
  line_seg r350 · faceplate`) whether or not the backup has a `DCSVRFY.DG`,
  and "show disabled" lists the empty slots. Elements stay data-only in the
  viewport — they are link-attached, and placing them honestly needs
  kinematics we don't parse yet.
- **Zones name their target models.** Each Cartesian zone's three
  `$MODEL_NUM` slots are resolved per the pendant's own legend (`Robot
  model` / `User model n` / `DISABLE`) with the referenced model's comment
  attached — in the 3d view's zone detail and, as a dim note beside the
  pendant's verbatim number, in the dcs tab's report view.
- **Lines-mode zones ground-truthed.** `$MODE=3` ↔ `Restricted zone(Lines)`
  (polygon keep-out, vertex count honored), `$MODE=0` ↔ `Working
  zone(Diagnal)` (keep-in) — confirmed against real controllers, both
  pendant vocabularies. Stop type 2 ↔ `Not stop` joins the confirmed map.
  `$MODE=2` remains unmapped and still says so.

## v1.1 — the 3D view (DCS zones drawn to scale)
- **New tab on the `0` key: "3d view".** DCS Cartesian Position Check zones
  drawn to scale from `DCSPOS.VA` — the authoritative vertex arrays, Z extents
  and (rotated) DCS user frames — cross-checked against the pendant's
  `DCSVRFY.DG` verify text (status, method, stop type) and stamped with the
  TCP position captured when the report was written.
- **Free orbit + the viewport cube.** Drag rotates with no limits — over
  the top and upside down are real (the world genuinely flips, axes and
  all); middle-drag (or shift+drag) pans, wheel zooms, `fit` or
  double-click resets, and rotation pivots about whatever sits at the
  viewport center, so pan-then-rotate stays on target. The cube in the
  top-right corner rotates with the view as a compass and snaps the
  camera to any of 26 directions — click a face, an edge, or a corner.
  Views are named per the FANUC world frame (right-handed, X forward /
  Y left / Z up); top and bottom are plan-oriented with X up-screen.
  A `perspective` button adds mild depth foreshortening; the default
  stays orthographic (parallel, true to scale). Vertical/horizontal
  rotate feel is invertible per-axis in settings (drag-down raises the
  camera by default).
  Auto-fit frames the scene's bounding sphere — one radius — so the view
  holds a steady scale while rotating, and the same mm-per-px carries
  across every view. Zone names, axis letters, the tcp marker and the
  scale ruler live on a fixed-size overlay: they anchor to the geometry
  but never grow, shrink, or drift with zoom and orbit.
  Hand-rolled SVG — no WebGL, no libraries — so it renders even on the
  software-rendering boot rescue path and adds nothing to the exe.
- **Side panel = every DCS check as a row.** Cartesian zones get a show/hide
  checkbox + a color swatch matching the viewport (keep-out drawn solid,
  keep-in dashed); joint position, speed checks and user models carry their
  data as honest list rows — nothing is drawn that would need a robot model
  we don't have. Every row expands to the same pendant-style detail block
  the dcs tab shows.
- Floor grid, world axes, base marker and a scale bar; "show disabled" lists
  every pendant slot; the tab remembers view, zoom and checkboxes per backup.
- Backups without `DCSPOS.VA` fall back to the verify report's Point 1 /
  Point 2 boxes — drawn without the frame rotation and flagged **approx**.

## LibraryImporter 0.1 — the hand-out library seeder (companion exe)
- **New standalone tool** (`dist/LibraryImporter.exe`, built from
  `packaging/libraryimporter.spec`): give a coworker the exe + a robots.json
  and their library exists in under a minute — no CLI, no BackupViewer needed
  on the machine first.
- Drag a robots.json onto the window (or click to browse), pick the plant
  folder (`Documents\RobotBackups\<Plant>`), tick lines or robots — per-line
  select-alls, one master "all", shift+click ranges, the same honest tri-state
  checklist as the app — and **import** writes `<LINE>\<FULL NAME>\robot.json`
  skeletons (schema 2, IP attached) that BackupViewer's scan adopts as-is.
- **Never duplicates:** robots already in the destination (same folder, or the
  IP already claimed anywhere in that line — even under a different name) show
  grayed "already in library" and are skipped again at write time. Safe to
  re-run as the list grows.
- Soft warnings when the destination looks wrong (the library root itself,
  nested too deep, a date-looking name the scanner would skip, or outside the
  machine's configured library).
- Carries the WebView2 boot rescue (one automatic software-rendering relaunch
  on the 0x8007139F field failure) and logs to
  `%LOCALAPPDATA%\LibraryImporter\app.log`.
- The seeding core is a stdlib-only module (`libraryimporter/core.py`) with a
  pluggable source-parser seam — a future DCDL parser (or absorbing the whole
  flow into BackupViewer 2.0) slots in without touching the GUI.

## v0.99q — the Design Assistant tab now actually appears
- **Fixed: no "design assistant" tab on DA 9.x portals.** Those portals never
  write the operator-page link into their HTML — the page builds it in script
  from each project row's name. The app now reads the project names straight from
  the portal's project table and builds the exact URL the portal itself builds
  (verified against two live cameras, both yielding the real
  `/DesignAssistant/<project>/default.htm` operator page).

## v0.99p — Matrox remote gets tabs (Design Assistant in-app)
- **The Matrox remote now has tabs: home + its Design Assistant page(s).** The
  portal normally pops its operator page (`…/DesignAssistant/<project>/default.htm`)
  into your default browser — the app now finds that page on the camera itself and
  shows it as a **second tab inside the remote panel**. With the tab in place, the
  portal's popup is suppressed, so nothing escapes to the browser. When a camera
  serves exactly one operator page, the panel opens **straight on it** (home stays
  one click away).
- **⟳ reload** and **open in window** act on the current tab, and every Design
  Assistant load gets a fresh cache-buster the same way the portal does it.

## v0.99o — Matrox remote, in-app
- **🖥 remote on Matrox cameras too.** A Matrox camera is operated through the web
  page it serves, so its remote button embeds that page **inside the app** — same
  fullscreen-capable panel as the CV-X remote, plus **⟳ reload** and an
  **open in window** button. If a camera's page refuses to be embedded, it opens
  in its own app window automatically.
- Both camera brands now share one rule: open the camera (files toolbar) or find
  it under its robot's photos tab, and hit **🖥 remote**.

## v0.99n — live CV-X remote desktop
- **Mirror a Keyence CV-X camera's live screen — right in the app.** Open a CV-X
  camera (or find it under its robot's photos tab) and hit **🖥 remote**: a
  fullscreen-capable panel shows the controller's live 1024×768 screen and forwards
  your **mouse** (move, left- and right-click) straight to it — no Keyence software,
  no separate Terminal PC. Press **f** for true fullscreen, **Esc** to close.
- **How it works.** The app speaks the controller's own remote-desktop protocol over
  its three TCP channels (reverse-engineered from packet captures), re-streams the
  JPEG frames over a private localhost feed, and maps your clicks back to screen
  pixels. It's a wholly separate path from the CV-X config backup — one live session
  per camera (don't connect while an operator or the Terminal is already on it).

## v0.99m — camera photos + auth polish
- **Matrox backups auto-authenticate.** The app now stages the camera credential in
  Windows Credential Manager (session-only, like Explorer's "remember me") and
  connects — so a fresh camera backs up **without the tech opening it in Explorer
  first**. Falls back through server-qualified login and session-riding, with a
  clear "open \\\\<ip> in Explorer" message only if all of it fails.
- **One photos tab, no greyed twin.** The robot's linked-camera view and a camera's
  own photos now share a single "photos" tab (the duplicate greyed tab is gone).
- **Photo hero: green-boxes / raw toggle + fullscreen.** Instead of links that
  dumped raw binary, the hero opens on the annotated **green boxes** image (the
  vision-tool overlay), toggles to the **raw** frame, and has a **fullscreen** view
  (click the image or the button; Esc closes).
- **"link cameras" resolves duplicate robot names.** When the same robot name exists
  under several lines, a camera auto-links to the robot in **its own plant+line** —
  so the button actually links instead of punting everything to manual.

## v0.99l — a robot's camera tab is labelled "photos"
- **A robot's linked-camera tab now reads "photos"** (was "cameras"), matching the
  tab you get opening a camera on its own — same data, consistent name. The tab id
  is unchanged, so a robot and a camera still never show two photo tabs at once.

## v0.99k — Matrox SMB login is more forgiving
- **Fixed "WinError 86" when backing up a Matrox camera.** On a workgroup laptop
  Windows sends its OWN name as the domain for a bare `mtxuser`, which the camera's
  Samba rejects. The connect now tries, in order: plain `mtxuser`/`MATROX`, then the
  server-qualified `<ip>\mtxuser` (forces the camera's local account), then riding
  an existing Explorer session (no creds), then a clear-and-retry — and if all fail
  it says exactly what to do ("open \\\\<ip> in Explorer, sign in as mtxuser, retry").

## v0.99j — linking to the right robot twin
- **Duplicate robot names no longer sabotage camera links.** Libraries carry the
  same robot under several lines (test-cell copies, legacy folders), and a camera
  linked to the wrong twin looked like "nothing happened." The camera's
  linked-robot picker now shows each robot's full **plant/line**, robots with
  linked cameras wear an **"N cams" pill** in the library (so you can see which
  twin holds the link), and the link-cameras toast now explains the **ambiguous**
  case (same robot name in several lines) instead of silently skipping it.

## v0.99i — cameras nest under their robot in the library
- **Linked cameras now group under the robot they inspect** in the library list
  (indented beneath it), so a bin-picker and its 2 MTX + 1 CV-X cameras read as one
  unit. Unlinked or cross-line cameras stay at top level. Pair with the **link
  cameras** button (auto-links Matrox by name; assign Keyence by hand in a camera's
  edit screen) and the robot's **cameras** tab, which already shows each linked
  camera's photos/inspection data with the same Photos-view feel.

## v0.99h — camera backups no longer die on long paths
- **Fixed the "cannot find the path" crash halfway through a camera backup.** A
  deep Matrox tree (`CAM1\Documents\Matrox Design Assistant\SavedImages\<date>\` +
  a long inspection filename + the `.part` temp suffix) pushes past Windows' legacy
  260-char path limit right when the backup reaches SavedImages. Downloads now use
  the `\\?\` extended-length path so any depth works.
- **A single unreadable/vanishing file is skipped, not fatal.** A live camera
  rotates SavedImages mid-backup; one missing file no longer sinks the whole pull —
  it's logged/skipped and the backup finishes (applies to Matrox and Keyence).

## v0.99g — discovery by EtherNet/IP identity + Matrox login fixes
- **Matrox cameras are discovered by EtherNet/IP identity, not just SMB.** One
  broadcast ListIdentity packet (the mechanism RSLinx uses) enumerates every
  industrial device on the subnet at once; Matrox cameras answer with ODVA vendor
  id 1144. This finds a camera **even if its SMB share is closed**, and is far
  cheaper than SMB-probing all 254 addresses. Live-confirmed: one packet found
  ~20 cameras on a plant /24. A camera identified this way but with no reachable
  share is still listed (flagged "no share") instead of vanishing.
- **Fixed the Matrox SMB login.** The account is the lowercase Linux user
  `mtxuser` (what a tech types in Explorer) — the app was sending `MTXuser`, which
  the camera's Samba rejects on a programmatic login. The backup also now **rides
  an existing Explorer session** to the camera when one is open (the proven path)
  instead of always forcing its own login, and clears a stale/conflicting session
  before authenticating.
- **Discovery scan port is spec-configurable** (SMB port joined the FTP port);
  robot/CV-X FTP discovery is unchanged.

## v0.99f — camera backups you can see, and Matrox over SMB
- **Camera backups now show up in the library.** A completed camera backup was
  invisible ("the program doesn't see the actual backup"): the folder scan only
  recognized FANUC file types, so a camera snapshot (a `CAM<n>/` tree + a
  `backup.json`) counted as "no backup." The scan now recognizes any snapshot by
  its `backup.json` sidecar (plus `da/`/`cv-x/`/`CAM<n>` camera folders), so
  camera history attaches to its entry like a robot's. **Validated live** against
  the existing CV-X pull.
- **Matrox cameras back up over SMB — the real transport.** Matrox cameras never
  appeared in discovery because they don't speak FTP at all: a Matrox smart camera
  is a **Samba** server (the `\\<ip>\mtxuser` share you reach in Explorer), not an
  FTP host — port 21 is closed. The whole Matrox path moved from FTP to SMB
  (`WNetAddConnection2` + a plain file copy — no new dependency, since SMB is
  native to Windows), and discovery now probes **port 445** so an SMB-only camera
  isn't skipped. **First live Matrox backup ever**: 550 files / 84 MB off a real
  GTX2000, re-opening straight into the photos tab; discovery classifies it as a
  Matrox camera named from its newest saved-image sidecar.
- **Robot / camera filter in discovery.** A segmented control by "select all"
  filters the scan results to **all · robots · cameras**, with live counts, so a
  cell full of robots doesn't bury the cameras (and vice-versa).

## v0.99e — Keyence cameras, over plain FTP
- **Keyence CV-X cameras back up over FTP — no C# helper needed.** Discovered
  live on the floor: a CV-X482D exposes an anonymous FTP server, lands on the SD
  card at `/SD1`, and serves its whole `cv-x/setting/` config tree. So Keyence is
  now a third device type (`camera-keyence`) that works exactly like the robot and
  Matrox paths — the old plan's proprietary `Vapi.Net.dll` C# helper is no longer
  required. **Validated against a live CV-X482D**: a real pull brought down 61
  files / 211 MB of the settings tree cleanly.
- **Discovery finds CV-X cameras too.** The subnet sweep now classifies a CV-X by
  its FTP banner and adds it to the library already typed as keyence camera
  (verified live alongside a FANUC robot on the same subnet).
- **Handles the CV-X FTP quirks.** The controller refuses pathful `RETR`/`LIST`
  (`550 Bad path`), so the downloader positions CWD per directory and transfers
  bare basenames. A FANUC backup mistakenly pointed at a CV-X now refuses loudly
  ("looks like a Keyence CV-X — set its device type to keyence camera").

## v0.99d — discovery finds cameras
- **Network discovery now finds Matrox cameras alongside FANUC robots.** The
  subnet sweep probes every non-FANUC FTP host with the MTXuser login and a
  `da/`/SavedImages sniff (cameras refuse anonymous login, so a 530 on the
  robot probe no longer ends the story). Discovered cameras show a CAM pill +
  model in the results, are named from their newest SavedImages sidecar
  ("Camera Name:"), and add to the library already typed as matrox camera —
  ready to back up without editing.

## v0.99c — test connection
- **"test connection" button in the add/edit device modal.** A read-only FTP
  probe of the form's current IP + device type: a robot answers with its MD:
  check, a camera with da/ + images checks, and a wedged controller or an
  off/moved camera shows up in seconds — instead of as a timed-out backup.

## v0.99b — field fixes
- **A robot backup pointed at a Matrox camera now refuses loudly** ("this host
  looks like a Matrox camera — set its device type to 'matrox camera'") instead
  of pulling a junk flat listing of the camera's home dir and choking on `da/`.
  The field symptom was "the backup ran but didn't grab the camera data" — the
  entry was still typed as a robot (or an older exe was used).
- **Backups no longer die on a settings-file rename race.** A multi-select
  backup fires many jobs at once; each persisted the library root, and on
  Windows the settings.json rename can hit "Access is denied" while any other
  handle has the file open — killing every backup before a single file was
  pulled. The write now retries the transient hold, only happens when the root
  actually changed, and can never fail the backup itself.

## v0.99a — cameras join the ecosystem
- **Matrox (MTX) smart cameras back up over FTP.** A camera is now a device type in
  the library alongside FANUC robots: add one with its IP, hit backup, and the app
  pulls its Design Assistant `da/` folder plus the newest day of `SavedImages` off the
  camera over plain FTP (default `MTXuser`/`MATROX` login) into the same dated-snapshot
  + Latest-mirror tree, using the same gentle, throttled, crash-safe engine. A station
  with several cameras pulls each into its own `CAM<n>` subfolder of one snapshot.
- **A photos view for what the camera saw.** Opening a camera backup lights up a
  **photos** tab: the most recent inspection image big with its pass/fail result,
  recipe, exposure and camera identity — parsed from the saved `.txt` sidecar — its
  per-tool blob/edge results, and a lazy-loading, pass/fail-filterable thumbnail grid
  of the rest. Robot backups are untouched; the viewer adapts to what's in the folder
  (a camera backup shows only photos + files).
- **Read-only camera probe / diagnose.** Before the first real pull, `diagnose` a live
  camera to confirm its FTP login + `da/`/`SavedImages` layout with zero writes — the
  same pre-flight safety the robot side has.
- **Under the hood:** the FTP engine's download + Latest-mirror steps are now shared
  primitives (`retrieve` / `mirror_latest`) reused by both the robot and camera jobs;
  `robot.json` gains a `device_type` (schema 3 — older sidecars read as `robot`).

## v0.99 — polish pass: one of everything, everywhere
- **Every checkbox list is the same checklist now.** Robot rows, the per-line
  select-alls, the fix-names rename/merge previews, and discover results all
  share one selection controller, so what works in one place works in all of
  them — shift+click selects the visible range everywhere, not just in the
  library. (The other lists were hand-rolled copies that never learned it.)
- **Select-all boxes stopped lying.** A line's box shows "−" when only some
  of its robots are selected — clicking it now clears the line (the minus is
  a reset) instead of silently selecting the rest. From an empty line one
  click still selects everything. The rename / merge previews gained the same
  "all" box.
- **Right-click expand/collapse leaves the clicked folder alone.** It folds or
  unfolds only what's under the folder you right-clicked — no more slamming
  the folder itself shut along with its children. (Expanding a closed folder
  still opens it, so the result is never invisible.) Same fix in the programs
  call-tree, which carried its own copy of the old behavior.
- **Text and chrome can go bigger.** Text size now reaches 24px (was 18) and
  chrome scale 160% (was 125%) — for high-DPI screens read at arm's length.
- **The library has a filter.** A search box in the library header narrows the
  tree as you type — by robot name, model, IP, or note text — and a matching
  plant/line name keeps its whole group visible. Groups render expanded while
  a filter is active (your fold state comes back when it clears), the match
  count shows in the box, and `/` focuses it. The selection toolbar acts on
  what you can see, never on filtered-out robots.
- **The compare picker IS the library now.** "Compare → from library" shows
  the same plant/line tree as the home screen (one shared tree component,
  not a look-alike), just stripped to click-to-pick rows and sized for a
  modal: LINE folders finally collapse (they were fixed labels), the open
  robot's plant starts expanded, folded groups show how many robots they
  hold, rows carry model + IP, and a filter box narrows the tree — finding
  one robot across 71 lines no longer means scrolling for it. Esc clears an
  active filter first; a second Esc closes the picker.
- **Filtering can't wipe your fold state anymore.** Rendering the tree
  expanded during a filter used to overwrite which folders you'd collapsed —
  clear the filter and everything sprang open. Folders now only remember
  toggles you actually click.
- **Every tab now remembers where you were.** io and registers — the two
  split-pane tables — get the same leave-and-return memory the other tabs
  already had: scroll, sort, category, in/out sides, register kind,
  "show empty", the active pane and the scroll lock all come back. system
  vars and mh valves now also remember WHICH records and branches you had
  open (and mh valves: the selected tool and collapsed valve cards), not
  just how far down you'd scrolled. All of it per-backup, in-session, like
  the rest.
- **"Merged" now means merged.** Merging a robot whose folder holds no dated
  snapshots (a flat copied-in backup, stray files) used to move nothing and
  still report "merged" — with both robots sitting there untouched. It now
  comes back "nothing merged — no dated snapshots to fold in", and a blocked
  merge changes truly nothing (no alias, no config fold, no writes). The
  same honesty applies when a rename/move collides into a merge.
- **Failed renames and moves say who failed and why.** The "3 rename(s)
  failed" count toast became per-robot reasons, shown verbatim for both
  fix-names and bulk "move to…" (long batches clamp to the first few).
- **Big text stopped crushing the valve cards.** At 20–24px text the
  MH-valves setup values squeezed into a one-character-wide column (12px
  wide, measured). Card grids' minimum width was a fixed 280/330px designed
  for 14px text; it now scales with the text size — measured at 24px text,
  the narrowest value column went 12px → 196px. The fix covers every card
  grid, so overview cards can't pull the same trick.
- **Discover got a real layout.** The modal is wide, network picker on the
  left, results on the right — the network row and the scan/add buttons are
  always visible and ONLY the results list scrolls (the whole modal body
  used to scroll, sinking the buttons below the fold on small screens).
  Falls back to one column on narrow windows.

## v0.99 — the folder tree is the whole truth
- **Where a folder sits is who the robot is.** The scan now derives every
  robot's plant/line/name from its folder's location — a stale `robot.json` or
  `backup.json` carried along in a copied tree can no longer teleport a robot
  into the plant/line it lived in years ago (the "I imported my old library
  into one plant folder and robots scattered everywhere else" field bug). A
  robot's home folder (the one carrying its sidecar id) always outranks a
  stray old-named copy.
- **`robot.json` no longer stores identity at all** (schema 2). The sidecar
  carries the stable id + config — IPs, FTP user, model, F-number, notes,
  aliases — and never plant/line/name: the folder hierarchy is the only
  source of that truth, so there is no second copy to go stale or fight the
  tree. Legacy schema-1 identity fields are ignored on read and shed whenever
  a sidecar is rewritten; a folder is recognized as a robot by the sidecar
  file's presence. (In-app renames still record the old name as an alias on
  the entry, so stray old-named folders keep re-merging.)
- **Empty folders are real structure.** An empty folder at the library root
  shows as a plant, an empty folder inside a plant as a line, and a folder at
  robot depth — even completely empty — as a robot with "no backup". Build
  your building's skeleton in Explorer, see it in the library, back it up
  from there.
- **Imported 2-digit-year snapshots recognized.** ERBU-era dated backups
  (`YY_MM_DD/HH_MM_SS`) group under their robot like the app's own snapshots
  (and sort/merge correctly) instead of spawning one pseudo-robot per date.
- **Merges need evidence, not a matching name.** Fix-names now confirms two
  entries are the same physical robot before suggesting a merge, using the
  field checklist: hostname, IP, F-number, master counts. 2+ matching signals
  = suggested (and it says which); exactly 1 = shown deselected with a ⚠ why;
  mismatched F-numbers veto outright (an F-number never changes). The FANUC
  factory hostname ("ROBOT") no longer counts as identity — backups reporting
  it used to merge into any robot that happened to be named ROBOT. Every
  merge row in the preview now has its own checkbox (like renames) plus both
  sides' backup counts, and merge targets prefer the convention-named /
  richer-history side. Merging remains strictly line-scoped.
- **Fix-names shows it's working**: the preview scan (it opens every selected
  robot's backup) now raises the global busy strip — "reading names from
  backups…" — instead of going silent for its longest step.
- **Refreshes stopped flashing.** Library refreshes repaint the robot tree in
  place — the old tree stays on screen until the new one is ready, and your
  scroll position survives. The folder watcher also no longer re-announces a
  change the app itself just made (the second, delayed flash after every
  rename/merge/backup).
- **`tools/apply_ip_list.py`**: stamp a building IP list (`{line: {robot:
  ip}}`) onto the tree as `robot.json` sidecars — dry-run by default, atomic
  writes, undo manifest, and a same-line duplicate-IP report for short-name /
  full-name twin folders.
- **`tools/seed_library.py`**: standalone, zero-dependency script to hand a
  coworker with the robot list — seeds their library (blank or existing) with
  every robot + IP under a plant they choose, expanding names to the plant
  convention (080R01 on line RBB01 → RB080R01B01) and silently skipping
  robots they already have by folder name or by IP.

## v0.98 — files are law (the field-feedback release)
- **The library IS your backup folder.** Every listing reflects the tree: folders
  copied in with Explorer appear (a background watcher refreshes within seconds),
  deleted folders disappear, and re-pointing the library folder shows exactly that
  folder's contents — old entries no longer tag along or lock up. An offline
  network drive still serves the last known library, marked stale, never wiped.
- **Adding a robot creates its real folder** (with `robot.json`, IP included), so
  discovery-added robots on a brand-new line survive rebuilds and PC moves. The
  "from backup" / "bulk from folder" import flows and the Delete button are gone —
  copy backups in / delete them with Explorer; hide covers the everyday case.
  Copied folders carrying a `robot.json` fold into that robot's history — and the
  scan now says so ("2 copied snapshots joined R01").
- **Merge, triple-checked.** The duplicate-skip path verifies file-for-file (both
  directions, byte content) before ever dropping a source copy; missing metadata
  and partial copies are conflicts, never deletions. Cross-volume moves stage in a
  `.__part` folder so a crash can't leave a half-snapshot at a real name. Merge
  keeps the folded robot's IPs/notes/config, and the confirm dialog shows the
  direction with a ⇄ swap button. Fix-names previews every rename first.
- **Never lose work.** Stray clicks can't destroy a half-built theme or typed
  form — dismissing warns first, and theme edits autosave a crash-proof draft
  with a restore offer. Closing the app during a backup asks before cutting it off.
- **The library screen at plant scale.** One sticky toolbar (selection-aware
  backup / hide / fix names / merge + counter) replaces the per-line button rows;
  sort by name, IP, or last backup; the compare picker groups by plant/line;
  discovery asks for plant & line at the add step, with pick-from-existing
  suggestions everywhere you'd otherwise retype them. Scan limits raised for
  plant-scale libraries (and truncation is reported, never silent).
- **Backups visible from every screen.** A global progress strip (aggregate bar,
  per-robot details, cancel) survives navigation and even a reload; slow
  operations show a "working…" pulse, and double-clicks can't run them twice.
- **Settings in one place, scaling that respects your data.** One ⚙ panel
  (appearance / text & scale / library folder). "Text size" now grows the DATA;
  the header/tabs/footer are pinned to a separate "chrome scale" — cranking the
  font no longer swells the chrome until nothing fits. The whole-page zoom (and
  its menu-positioning workarounds) is retired.

## v0.9 — ecosystem shell + backup taker
- **Home menu**: open a backup, add one to the library, or take a new one.
- **Robot library** organized PLANT / LINE / ROBOT, persisted locally; add manually
  or import an existing backup folder.
- **Take a new backup**: connect to a FANUC controller over FTP and pull an
  "all of above" backup (the `MD:` device) into a `Latest`-mirror + dated-history
  tree, with a pre-flight reachability probe and live progress.

## v0.82 — compare polish
- Compare screen: refresh, hide individual entries, persistence, in-place filter;
  dropped binary/metadata-only program changes to declutter.

## v0.8 / v0.81 — trust pass
- Bug-batch correctness pass; added the MH Valves and Payloads views.

## v0.76 — composable primitives
- Extracted pills / segmented / table / frame-card / card-hero-kv / drag into shared
  builders; migrated every tab onto them.

## v0.75 — more data
- System-vars tab, KAREL `.PC` programs, DCS rework, program-to-program hops.

## v0.7 / v0.7.1 — DCS
- DCS tab: verify report, change history, signatures; section menu, drop-down
  details, code-styled safe-I/O logic.

## v0.6 — dashboard + compare
- Overview dashboard cleanup and a compare workflow for verifying programs fast.

## v0.3 — feedback round
- Alarm history, search fixes, IO configuration view, linked scroll-lock.

## v0.1 — first cut
- FANUC robot backup viewer: overview, frames, IO, registers, programs, alarms, files.
