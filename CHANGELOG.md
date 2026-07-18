# Changelog

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
