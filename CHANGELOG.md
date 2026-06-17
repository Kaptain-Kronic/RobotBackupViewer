# Changelog

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
