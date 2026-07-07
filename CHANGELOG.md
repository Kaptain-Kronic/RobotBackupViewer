# Changelog

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
