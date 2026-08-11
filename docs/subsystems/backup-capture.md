# Backup capture — jobs that touch live equipment

*Subsystem doc #2. Written 2026-08-01 against `main` @ `b6caaaf`, clean tree
(plus this pass's own one-line docstring correction in `ftpbackup.py`, §9
item 1 — a 1-for-1 line swap, so cites into that file hold).
Line-number cites are against that revision and drift with edits; the anchor
commit is the reference.*

Covers: src/backupviewer/ftpbackup.py, src/backupviewer/keyencebackup.py,
src/backupviewer/mtxbackup.py, src/backupviewer/discover.py,
src/backupviewer/backuplog.py
(5 files)

*It also describes the job plumbing those five share — `api.py`'s device
registry and backup/scan endpoints, `web/js/jobs.js`, the launch and retry flows
in `tabs/home.js` and `manage_ui.js`, and `library.py`'s registration half — but
does not claim those files: each is owned by the subsystem it belongs to, and is
referenced here rather than documented here.*

> **Template note.** parsing.md's shape mostly holds, with one deliberate
> amendment: this doc has a dedicated **§6 Failure modes** between Invariants
> and Traps, making ten sections instead of nine. Parsing didn't need it —
> pure functions don't die mid-flight. Capture jobs do, and "what the disk,
> the UI and the log say after each kind of death" is this subsystem's actual
> product, too big to bury inside Invariants and distinct in kind: invariants
> are what must stay true, failure modes are what happens when the world
> breaks anyway. Recommendation for doc #3+: keep the section for anything
> stateful (scanning, library, remotes); drop it for pure layers.
>
> The evidence tags also shift: **pendant-paired** has no analogue here (no
> pendant answers for a transport). Its slot is taken by **live-run
> \<date\> (recorded)** — a behaviour discovered against real hardware on a
> stated date and recorded in code/CHANGELOG, distinct from **live-run
> 2026-08-01**, which I re-measured today against local evidence.
> **corpus-measured** and **assumed** keep their meanings. Where a recorded
> quirk is also *enforced by the offline fake* (a regression fails tests, not
> just a comment), the evidence column says so — for transport truths that
> enforcement is what keeps them true.
>
> Division of labour as before: CLAUDE.md owns the rules ("Gentle with live
> equipment" *is* this subsystem's contract), INVENTORY.md owns per-file
> breadth. This file holds ground truth + how it was verified, cross-file
> invariants, failure behaviour, and paid-for traps.

---

## 1. What it is

Everything that dials plant equipment lives here — the only code in the app
with consequences beyond the local disk. Three backup transports pull
snapshots into the library tree: FANUC controllers over FTP
(`ftpbackup.BackupJob`), Keyence CV-X cameras over anonymous FTP
(`keyencebackup.KeyenceBackupJob`), Matrox cameras over SMB
(`mtxbackup.CameraBackupJob`). `discover.py` sweeps a subnet to *find* those
devices (FTP probe + EtherNet/IP identity broadcast + gated SMB), and
`backuplog.py` is the durable memory of what each run did. Everything is
read-only toward the device: the jobs GET files, the probes list and read —
no capture module contains a single FTP/SMB write verb toward equipment
(grep-verified 2026-08-01: no `storbinary`/`storlines`/`DELE`/`MKD`/rename
anywhere under `src/backupviewer/`). Writing happens only locally, into the
library tree — and never into an existing backup folder.

The boundary with neighbours: `api.py` owns job construction (the
device-type registry), threading, polling and cancellation; `jobs.js` owns
watching (one 500 ms poll for however many jobs); `library.py` owns what a
finished snapshot *means* (registration, partial-never-latest, camera
self-naming); `session.py`/parsers own reading the result. A deliberate
asymmetry with parsing: parsers are pure and know nothing of I/O; capture is
*all* I/O and never parses — the two touch only at `resolve_robot_name`
(discovery feeding a SUMMARY head to `summary_dg`) and `name_from_backup`
(camera jobs feeding pulled sidecars to `cvx_inspect`/`mtx_saved_image`).

## 2. The files

Per-file descriptions live in the [INVENTORY map](../INVENTORY.md). Its
`subsystem` column assigns **backup capture** four files (`ftpbackup.py`,
`discover.py`, `backuplog.py`, `jobs.js`); `keyencebackup.py` and
`mtxbackup.py` are primary **cameras** with capture as their second home, and
`home.js`, `manage_ui.js`, `library.py` carry capture as a secondary role.
This doc covers the union — the job machinery is one subsystem regardless of
which brand's folder a transport sits in.

Structure worth knowing before touching it:

- **`ftpbackup.py` is the shared base, not just the FANUC engine** (INVENTORY
  §C already flags the understatement). It owns the transfer primitives every
  transport reuses (`retrieve`, `_copy_file`'s pattern, `mirror_latest`,
  `long_path`, `dated_dir`/`latest_dir`), the read-only FTP walk the CV-X job
  imports (`_names`/`_walk`, `ftpbackup.py:158-201`), and the two job base
  classes: `_JobBase` (progress dict, cancel, crash-safety marker, mirror,
  library record — `ftpbackup.py:204-338`) and `CameraJobBase` (the
  multi-camera station loop, `ftpbackup.py:341-409`). The Matrox job inherits
  from a module named "ftp" while speaking SMB; tolerated, like
  `session.py`'s import of `long_path` (parsing.md §8.8), until the shared
  parts find a neutral home.
- **One brand = one registration, not an if/elif chain.** `_DEVICE_REGISTRY`
  (`api.py:203-247`) maps `device_type` → probe / diagnose / job class /
  per-brand credential defaults. `""` is the FANUC default row. Adding a
  brand is one row + one module.
- **Two vocabularies deliberately twinned:** `ftpbackup.is_terminal`
  (`ftpbackup.py:45-51`) is the one Python home of "this job is over";
  `jobs.js:33-35` keeps the JS copy, and both files say so — a future status
  lands in both or the strip lies.
- `discover.py` also carries the adapter enumeration for the discover dialog
  (PowerShell via absolute path — `discover.py:106-198`) and the read-only
  `diagnose_*` probes whose JSON lands in app.log for shop-PC debugging
  (`discover.py:601-669`, `keyencebackup.py:301-359`, `mtxbackup.py:376-409`).

## 3. The flow

One backup, end to end (batch = this × N robots, all sharing a `run_id`):

```
home.js startLineBackup (home.js:1531)        one click = one run
   │  shared password modal (1572-94): prompted per run, held in a JS local,
   │  sent only for entries with ftp.user set; runId stamped per click
   ▼
api.start_backup (api.py:3291) ──> _DEVICE_REGISTRY row ──> job_cls(spec)
   │  run_id: an in-flight run ADOPTS the new job (api.py:3347-53)
   │  backuplog.start_job: spec recorded SANITIZED - passwd never in it
   │  daemon thread: job.run() then backuplog.finish_job (api.py:3363-70)
   ▼
job.run()                                      one connection, sequential
   │  dated = <root>/<plant>/<line>/<robot>/<YYYY_MM_DD>/<HH_MM_SS>
   │  backup.json written FIRST, complete:false        (the started-marker)
   │  per file: RETR/copy -> <name>.part -> os.replace; throttle between
   │  _write_sidecars LAST: notes.txt + complete:true + skipped list
   │  Latest mirror: copy to sibling .__tmp, atomic swap  (never the only copy)
   │  on_complete (guarded): library.register_backup + camera self-name/link
   ▼
jobs.js 500ms poller (list_backup_jobs) ──> jobstrip + per-row bars
   │  run-scoped: finished robots stay in the denominator (jobs.js:40-54)
   ▼
backuplog (%APPDATA%/backup_log.json) ──> manage_ui "last run" + retry-failed
```

Discovery (`NetworkScanJob.run`, `discover.py:381-402`): one EtherNet/IP
ListIdentity broadcast up front (Matrox answers with ODVA vendor 1144), then
a 48-thread sweep where each host gets, sequentially: a 0.7 s TCP pre-check
on port 21 → FANUC probe → (if FANUC) a second connection to resolve the
name, or (if the banner says CV-X) the Keyence probe; hosts the broadcast
named Matrox get a port-445 pre-check → SMB probe → sidecar name read.
Parallelism is *across* hosts; per host every touch is sequential.

What's stateful: each job/scan is one object holding a lock-guarded progress
dict (`snapshot()`/`cancel()`), registered in `api._jobs`/`api._scans` and
polled by id. Nothing capture-side is cached between runs; the durable state
is the disk tree plus `backup_log.json`.

## 4. Domain truths

How each was verified, or an honest **assumed**. Tags per the template note.

### FANUC over FTP

| Fact | Evidence |
|---|---|
| The pull is the `MD:` device — the ASCII set the controller *synthesizes on GET* ("all of above"). A true IMAGE backup needs TFTP + a reboot into the boot menu, deliberately out of scope: the viewer must never be a thing that reboots a production controller | scope confirmed with the owner, recorded `ftpbackup.py:4-8`; the type string is literally `"all of above"` (`ftpbackup.py:417`) |
| `MD:` is flat — one `nlst`, no per-name CWD probing needed (and none done, to keep the touch light) | `ftpbackup.py:519-538`; **live-run (recorded)** via the `--diagnose` captures that shaped `discover.py:46-55` |
| Some controllers root straight at `MD:` and refuse `cwd("MD:")` — both the backup enumerate and the name resolver tolerate it | `ftpbackup.py:526-535`, `discover.py:467-484`; fake-enforced (`test_discover.py:409-422`) |
| The MD: listing is alphabetical, program `.ls` first (`-bcked*`, `abortit`, …), report files buried — so naming by "first .LS files" reads programs with no header; report files must be RETR'd **by name**. Some controllers even hide reports from `nlst` while serving them on GET | `discover.py:51-55,506-536`; **live-run (recorded)** R-30iB via `--diagnose`; fake-enforced (`test_discover.py:252-303`); **live-run 2026-08-01**: the private PARTIAL pin's file list is exactly this order, see below |
| On a live R-30iB, `RETR ERRALL.LS` reset the data connection while `LOGBOOK.LS` read cleanly — hence LOGBOOK first in the shortlist | `discover.py:51-55`; **live-run (recorded)**; fake-enforced (`test_discover.py:270-303`) |
| SUMMARY.DG's identity block sits in the first lines, so a 24,000-byte capped head-read yields model + F-number without slurping a live robot; the robot NAME ($HOSTNAME, ethernet section) is far too deep for the cap — on a real dump it comes from a report-`.LS` header instead | `discover.py:46-50,487-503`; **live-run 2026-08-01** on the primary fixture's real SUMMARY.DG: 156,392 bytes, `F Number` at byte 79, `$HOSTNAME` at byte **150,572** — 6.3× past the cap |
| A report-`.LS` first line is `<file>  Robot Name <host> <date>` — the same header `session.py` classifies on; discovery reuses that regex (`session._REPORT_HEADER`) rather than a private copy | `discover.py:506-536` |
| `.IMG`/`.IMR` image artifacts are skipped (not an FTP backup's job) and the skip is *recorded* — in the snapshot **and** in `backup.json` | `ftpbackup.py:40,550-552`, skipped-list plumbing `ftpbackup.py:301-310`; pinned by `test_ftpbackup.py:143-164` |
| Blank user/pass = anonymous login; sites with FTP auth set `ftp.user` on the library entry and the batch flow prompts one shared password per run | `ftpbackup.py:504`, `home.js:1538-1550` |

### Keyence CV-X over FTP

| Fact | Evidence |
|---|---|
| A CV-X exposes an **anonymous** FTP server; the login lands on the SD card at `/SD1/`; settings live under `cv-x/setting/` (`cv-x/box/` = big saved-set blobs, optional pull) | **live-run 2026-07-13 (recorded)**, CV-X482D on the shop floor — `keyencebackup.py:4-13`, layout notes in `CVX_FTP_LAYOUT.md`; the old plan's proprietary `Vapi.Net.dll` helper proved unnecessary |
| The CV-X FTP **refuses pathful operations** ("550 Bad path"): no `RETR a/b/c`, no multi-segment `cwd` — you CWD segment by segment and RETR bare basenames. The (Linux) Matrox ftpd of the older plan resolved full relpaths; this quirk is why `_pull_camera` repositions CWD per directory | **live-run (recorded)** — `keyencebackup.py:24-27,114-160`; fake-enforced: `FakeCVX` raises on any pathful RETR/cwd, so a regression fails the suite (`test_keyencebackup.py:66-98`) |
| Keeping the `SD1/` level in the snapshot + dropping `workspace.xml` beside it makes each camera folder a **CV-X Simulator workspace** — a backup opens in the simulator with no export step | `keyencebackup.py:15-18,174-186`; byte-format proof lives with `keyence_workspace` (pinned by `test_keyence_workspace.py`); asserted end-to-end incl. the mirror (`test_keyencebackup.py:186-200`) |
| The manifest is written only when the camera actually yielded files — a workspace.xml over an empty tree would promise the simulator a settings tree that was never pulled | `keyencebackup.py:161-162,174-186`; pinned (`test_keyencebackup.py:214-230`) |
| A CV-X carries no controller name over FTP (banner = model only); identity comes from its inspection *program* names, read from the pulled snapshot after the backup (`cvx_inspect`, parsing.md §4) | `keyencebackup.py:207-246`; the live-FTP half of that claim is parsing.md's, re-used not re-proven here |

### Matrox over SMB

| Fact | Evidence |
|---|---|
| A Matrox DA camera is embedded Linux running **Samba**; port 21 is closed and SSH refuses the DA login — the transport is exactly what a tech does in Explorer: `\\<ip>\mtxuser` | **live-run 2026-07-14 (recorded)** — `mtxbackup.py:4-8`; CHANGELOG v0.99f ("First live Matrox backup ever: 550 files / 84 MB") |
| Credentials are the vendor defaults burned into every DA camera, and **both are case-sensitive**: user `mtxuser` all-lowercase, password `Matrox` Title-case. `MATROX`/`matrox` are refused server-side (STATUS_LOGON_FAILURE) | **live-run 2026-07-20 (recorded), two cameras** — `mtxbackup.py:49-58`. This comment is why first backups stopped failing (§7); the leave-them-in-source ruling is INVENTORY §E's |
| Backup scope: the whole `da/` tree + **only the newest** `SavedImages/<YYYY-MM-DD>/` date folder — small, fast snapshots that still carry the latest photo | scope confirmed with the owner, `mtxbackup.py:17-18`; enforced `mtxbackup.py:251-264`, pinned (`test_mtxbackup.py:105-109`) |
| A live camera **rotates SavedImages mid-backup** — a vanished file is skipped + logged, never a sunk pull | **live-run (recorded)**, CHANGELOG v0.99h; `mtxbackup.py:338-343` |
| Windows allows **one credential set per server** (error 1219), and a WORKGROUP PC sends its *own name* as the domain for a bare username (refused as 86/1326) — hence the connect ladder (§7) and the ride-an-existing-Explorer-session-first rule | `mtxbackup.py:71-77,162-246`; **live-run (recorded)** — the ladder's steps each correspond to a field failure, CHANGELOG v0.99k/v0.99m |
| The camera's own name lives in the newest SavedImages `.txt` sidecar (`Camera Name:` / `Camera Type:`) — readable live (`resolve_camera_name`) or from any pulled snapshot (`name_from_backup`), which is how a camera discovered as a bare IP self-names after its first backup | `mtxbackup.py:412-470`; end-to-end incl. folder rename + auto-link pinned (`test_mtxbackup.py:292-336`) |

### Discovery

| Fact | Evidence |
|---|---|
| One EtherNet/IP **ListIdentity broadcast** (UDP 44818, encapsulation command 0x63 — the mechanism RSLinx uses) enumerates every industrial device on the subnet at once; Matrox answers with ODVA vendor id **1144**. Read-only, transport-independent — finds a camera even with its file share closed | `discover.py:38-44,232-288`; **live-run (recorded)**: 21 cameras on one /24 (`discover.py:42`); reply layout (vendor@48, serial@58, len-prefixed product@62) pinned synthetically (`test_discover.py:368-383`) |
| The broadcast is sent **twice**: on a busy shop /24 single replies are lost to UDP collisions; replies are deduped by source IP | `discover.py:246-262`; **assumed** proportion (collision loss observed, not quantified) |
| SMB is probed **only** on hosts the identity broadcast already named Matrox — authenticating `mtxuser`/`Matrox` against every open-445 host (PCs, HMIs, file servers) would spray failed logons and disturb techs' own sessions (1219) | `discover.py:432-440`; spy-tested: an open-445 host with no identity is *never* touched (`test_discover.py:340-365`) |
| A camera identified by EIP but with its share closed is still reported (`backup_ready:false, via:"eip"`) for manual handling — found ≠ backable, and vanishing would be dishonest | `discover.py:440-458`; pinned (`test_discover.py:385-406`) |
| A CV-X announces itself in the FTP banner ("CV-X…"); banner sighting gates the Keyence probe, and `has_setting` (not mere anonymous FTP) is what marks a real camera | `discover.py:419-430`, `keyencebackup.py:253-255` |
| A bare IP typed in the discover dialog means its /24 — nobody on a shop floor should need CIDR notation | `discover.py:83-94`; pinned (`test_discover.py:95-101`) |
| Adapter enumeration shells PowerShell **by absolute path** (`C:\Windows\System32\...\powershell.exe`) — PATH is unreliable inside a frozen onefile exe — with `CREATE_NO_WINDOW` so the windowed exe never flashes a console | `discover.py:106-136`; parse pinned, spawn injectable (`test_discover.py:448-472`) |

### The tree on disk

| Fact | Evidence |
|---|---|
| Dated snapshot: `<root>/[<plant>/]<line>/<robot>/<YYYY_MM_DD>/<HH_MM_SS>` (plant omitted when blank, so the tree degrades to LINE/ROBOT); mirror: `<root>/[<plant>/]<line>/Latest/<robot>` | `ftpbackup.py:64-81`; this path *shape* is also `library.py`'s identity source (`_path_identity`) — the two must stay agreed |
| `backup.json` is the **started-marker**: written `complete:false` the moment the dated dir exists, flipped `true` only by `_write_sidecars`, the LAST step of a successful pull. Cameras and robots alike | `ftpbackup.py:283-310,376-379,458-463`; pinned three ways (`test_ftpbackup.py:199-237`, `test_mtxbackup.py:218-232`, `test_keyencebackup.py:214-230`) |
| The marker is *itself* crash-safe: written to `backup.json.tmp` then `os.replace`d, so a crash mid-marker-write can't leave a half-written JSON that parses as garbage | `ftpbackup.py:297-299` |
| `complete:false` is the **only** thing that marks a snapshot partial. A sidecar-less folder is a hand-import (pre-marker era / foreign tree), *not* a partial — deliberate: flagging every import partial would cry wolf | `library.py:587-594`; boundary consequences in §5 inv. 6 and §9 item 5 |
| The pre-marker failure shape is real, preserved data: the private PARTIAL fixture pin is a truncated 2026-07-07 pull — eight days before the marker shipped (`a82a9b1`, 2026-07-15) | **live-run 2026-08-01**: 195 files, zero `.part`, no `backup.json`/`notes.txt`, and every basename sorts ≤ `FRAME.DG` — the connection died mid-alphabet, so the A–F files (53 of them `.VA`/`.DG`) look plausible while NUMREG, SYSTEM, SUMMARY and everything G–Z simply aren't there. Exactly the lie the marker exists to prevent |

## 5. Invariants

What must stay true, what enforces it, what breaks if it doesn't. The first
five are CLAUDE.md's "Gentle with live equipment" made mechanical.

1. **Toward equipment, capture only reads.** Jobs GET; probes/diagnoses
   list and read (`probe_controller` "NO writes, NO downloads",
   `ftpbackup.py:585-588`; both camera probes "ZERO writes"). No FTP/SMB
   write verb exists in the codebase (§1). Break it and the app's core
   promise — "reads evidence, does not modify robots" — is gone; write-back
   is a future, separately-gated tier (ROADMAP 2.0).
2. **One connection per device at a time, files sequential.** Enforced by
   shape, not by a lock: each job owns exactly one FTP handle / SMB mount
   per camera and pulls files in a plain loop
   (`ftpbackup.py:444-497`, `keyencebackup.py:114-172`,
   `mtxbackup.py:319-349`); a station's cameras run sequentially
   (`ftpbackup.py:384-394`); discovery touches each host sequentially
   within one worker (`discover.py:404-458`). Parallelism exists only
   *across* devices (one thread per job; 48 scan workers). Nothing stops
   two *jobs* aimed at one robot — see §6.4 for what happens then.
3. **Throttled and capped.** 30 ms between files on both FTP transports
   (`throttle=0.03`, `ftpbackup.py:425`, `keyencebackup.py:97`); listing
   caps (`FR_MAX_FILES` 5000 on the FANUC enumerate, `WALK_MAX_FILES`
   20,000 on the CV-X walk) that log when hit; the name resolver reads a
   24k head and *aborts the transfer* at the cap so a live robot isn't
   slurped dry (`discover.py:487-503`). The Matrox job's default throttle
   is **0.0** (`mtxbackup.py:311`) — see §9 item 3.
4. **Bounded retries, human-initiated re-runs.** Per *file*: at most 2
   retries with 0.4 s/0.8 s backoff (`retrieve`, `_copy_file`). Per *job*:
   zero automatic retries — a failed robot is re-fired only by a person
   (the retry-failed button → `retry_failed_backups`, `api.py:3380-3400`),
   and `backuplog` counts `attempts` so the record shows it. No layer
   retries the whole batch. Break this and a dead controller gets hammered
   in a loop by an unattended PC.
5. **Timeouts everywhere — with one honest exception.** Every ftplib
   factory call passes `CONNECT_TIMEOUT` (20 s), which ftplib applies as
   the socket timeout to the control *and* derived data connections;
   discovery pre-checks ports with 0.7 s TCP probes so a /24 sweep never
   waits 20 s on a dead host; EIP listens 1.5 s; the PowerShell child gets
   5 s. The exception: `WNetAddConnection2W` has no timeout parameter —
   a half-dead Samba can hold a thread for the OS's own timeout (§6.6).
6. **Partial never becomes latest.** The started-marker (§4) is only half
   the contract; the other half is every consumer honouring it:
   `library._pick_latest` skips partials for `latest_path`/`last_backup`
   (`library.py:597-605`), merge never adopts a partial as the mirror
   (`library.py:1176-1177`), `resolve_open_path` falls back to the newest
   COMPLETE snapshot and opens a partial only when it is literally all the
   robot has (`library.py:399-420`), and the home row pills the state
   honestly — including *suppressing* the partial pill while a pull is
   running, because mid-pull the newest snapshot is partial *by design*
   (`home.js:722-731`). Scope limit: this protects against deaths of OUR
   engine; a foreign/pre-marker partial is indistinguishable from an
   import (§4) and walks past all of it.
7. **The dated snapshot is immutable truth; the mirror is a convenience
   built beside and swapped in.** `mirror_latest` copies to a sibling
   `.__tmp`, then atomic-`os.replace`s onto `Latest/<robot>`; any failure
   is logged and returns None — the dated snapshot is never the thing at
   risk (`ftpbackup.py:136-155`). Same idea per file: `.part` then
   `os.replace`, so no half-file ever wears a real name
   (`ftpbackup.py:102-133`, `mtxbackup.py:267-291`).
8. **Passwords are prompted per run, live in memory, and die with the
   run.** The chain, verified end to end 2026-08-01: one shared prompt per
   batch click, value held in a JS local (`home.js:1572-1594`) → sent in
   the spec → job attribute only; the progress dict (what `snapshot()`
   returns, what `jobs.js` polls, what `backup.json` records) never
   contains it → `backuplog.start_job` records the spec through
   `sanitize_spec`, whose key whitelist simply lacks `passwd`
   (`backuplog.py:37-38,69-70`) — and `test_backuplog.py:25-34` asserts
   the literal password string appears nowhere in the written file →
   retry re-prompts (`manage_ui.js:188-198`), applying the new password
   only to specs that carry an FTP user (`api.py:3380-3400`). The one
   place a credential touches an OS store: the Matrox job stages the
   camera login in Windows Credential Manager with
   `CRED_PERSIST_SESSION` — Explorer's own "remember me", cleared at
   logoff, never written to disk — and deletes it in the mount's cleanup
   so it lives only for the pull (`mtxbackup.py:113-159,220-246`). Caveat
   in §6.5.
9. **Registration can never fail a finished backup.** `_fire_on_complete`
   swallows and logs everything (`ftpbackup.py:268-275`); camera
   self-naming and auto-linking are best-effort behind the same guard
   (`api.py:3320-3345`); `backuplog.finish_job` failures are caught in the
   worker (`api.py:3363-3368`); a workspace.xml write failure leaves the
   pulled settings intact (`keyencebackup.py:174-186`). The files on disk
   are the achievement; bookkeeping is subordinate.
10. **Identity teach-back goes through the folder, not just the registry.**
    A camera's learned name renames its FOLDER via `relocate_robot`
    (files are law — a registry-only patch was reverted by the next scan,
    the original bug), keeps the placeholder as an alias, and never
    overwrites a human-typed name (`library.py:293-328`); auto-link
    matches the STATION+ROBOT key parsed from the camera's own name,
    falls back to exact same-name, prefers the robot in the camera's own
    plant/line cell on ties, and links only when unambiguous
    (`library.py:269-275,342-396`). Pinned end-to-end incl. surviving a
    rescan (`test_mtxbackup.py:292-336`, `test_camera_link.py`).
11. **run_id groups one user action, and an in-flight run adopts
    late-comers.** The frontend stamps one id per click
    (`home.js:1540-1547`); while any job is live, *every* new job joins
    that run regardless of its stamp (`api._active_run_id`,
    `api.py:3316-3325,3347-3353`) so a mid-run retry lands in the same
    "last run" report; `backuplog` mirrors the same grouping durably
    (replacing only SETTLED rows, counting attempts — `backuplog.py:85-110`)
    and `jobs.js` derives the same run-scoped denominator from snapshots
    so the bar only climbs (`jobs.js:12-16,40-54`). Three views, one
    grouping; change one and the strip, the log and the report disagree.
12. **The library watcher yields to backups.** The 4 s signature poller
    skips every tick while a backup runs — jobs write thousands of files
    into the watched tree (`api.py:303-314,348-365`), and the post-backup
    refresh already repaints once, honestly.

## 6. Failure modes

The section parsing.md didn't need. Format: what dies → what the disk, the
UI and the log say afterwards. "Verified" = a test pins it; "traced" = read
off the code this pass, no test; anything weaker is tagged.

1. **The FTP/SMB connection dies mid-pull (robot job).** The in-flight
   `.part` is deleted, the file retried ≤2× with backoff; when the last
   retry dies, the job finishes `error` with the exception text. Disk: the
   dated folder holds every completed file, zero `.part`, and
   `backup.json complete:false`; no `notes.txt`, no Latest mirror
   touched. UI: red row + strip counts it failed. Log: the run row goes
   `error` with the message; retry-failed re-fires the sanitized spec.
   Library: the snapshot registers as nothing (on_complete never ran) and
   the next scan adopts it only as a `partial`-pilled history row, never
   latest. Verified (`test_ftpbackup.py:199-237` + `test_library.py`).
2. **One file is unpullable but the device is alive.** Deliberately
   different per transport. Camera jobs skip the file, log it, append it
   to `skipped`, and keep going — a rotated SavedImages photo or one
   locked `.dat` must not sink a station pull
   (`keyencebackup.py:153-157`, `mtxbackup.py:338-343`). The FANUC job has
   no per-file skip: any file that survives its retries as dead kills the
   job (traced — `_download_one` raises through `run`). That asymmetry is
   a judgment call recorded here: a robot backup with silent holes is
   worse than a failed one (it *becomes* the reference copy), while a
   camera snapshot's photos are inherently rolling. Either way the loss is
   *written down*: `skipped` rides `backup.json` off the job itself, so a
   caller can't forget it (`ftpbackup.py:301-310`, the `85dd58d` lesson),
   and a "done" job with skips wears a warning "!" in the strip, never
   the clean ✓ (`jobs.js:269-281`).
3. **A whole camera fails in a multi-camera station.** Its exception is
   captured as an error string; the *other* cameras still pull; the job
   errors only when NOTHING was pulled, else finishes done-with-errors
   (`ftpbackup.py:384-399`). Verified for the transports' shared base
   (`test_mtxbackup.py:218-232` drives the nothing-pulled arm).
4. **Two backups target one robot.** Nothing prevents it: the row menu has
   no backup action (batch-only), the batch fires each robot once, and
   `isRobotActive` only suppresses cry-wolf pills — but two overlapping
   batch clicks can double-fire a robot, and `backuplog` even models it
   ("a row still running is a real concurrent job", `backuplog.py:94-106`).
   Traced consequences: two FTP sessions against one controller (the one
   real breach of invariant 2's spirit); distinct start-seconds → distinct
   dated dirs, both valid; both then race `mirror_latest` at the same
   `Latest/<robot>` — worst case one copytree loses to the other's rmtree,
   is *logged*, returns None, and the loser's dated snapshot stays intact,
   so the race can cost a mirror but never a backup. The genuinely bad
   interleaving — same robot, same wall-clock **second** → both jobs share
   one dated dir and interleave writes under one marker — is not reachable
   from the shipped UI (two deliberate clicks through a modal in <1 s).
   Not test-pinned; recorded as §9 item 4.
5. **The app is killed / the PC loses power mid-run.** Backup threads are
   daemons; closing the window intentionally asks first ("N backups still
   running… Close anyway?") and fails OPEN if the dialog itself breaks —
   never trap the user in the app (`api.py:330-346`; verified,
   `test_close_guard.py`). After a hard death: disk shows the
   `complete:false` snapshot (per-file `.part` protocol means no half-file
   wears a real name); `backuplog` keeps the honest `running` rows — "a
   crash mid-run leaves honest running rows rather than nothing"
   (`backuplog.py:14-15`); on next launch the strip's boot seed re-lists
   jobs from the (now empty) registry, so dead rows age out of the UI
   while the log keeps the record. Two traced leaks, neither
   data-corrupting: a Matrox credential staged in Credential Manager
   survives until logoff if cleanup never ran (session-persist bounds it;
   §5 inv. 8), and a `Latest/<robot>.__tmp` from a mirror killed mid-copy
   sits until the next successful mirror rmtrees it
   (`ftpbackup.py:144-145`).
6. **The disk fills.** OSError is inside `ftplib.all_errors`, so for the
   FANUC job a full disk behaves like case 1: bounded pointless retries,
   then a clean `error` with the marker false (traced). Camera jobs treat
   it as case 2 — every remaining file "skips", and a pull that landed
   *some* files finishes done-with-skips: honest (the skip list and "!"
   flag say exactly what's missing) but easy to misread as success at a
   glance. A half-dead SMB host is the worse cousin: `WNetAddConnection2W`
   and share-path stats have no app-level timeout (§5 inv. 5), so a
   camera job's thread can hang for the OS's own timeout — cancellation
   only takes effect between files. Traced, not tested.
7. **Discovery on a hostile or odd subnet.** Broadcast blocked → EIP
   returns `[]` and Matrox discovery degrades to nothing (never to
   credential-spraying — the SMB gate holds, spy-verified). A host that
   accepts anonymous FTP but is neither FANUC nor CV-X is ignored (banner
   + `has_md` + `cv-x/` sighting all fail). A worker's exception marks
   that host scanned-with-nothing, never kills the sweep
   (`discover.py:389-398`). Cancel flips the job `cancelled`; in-flight
   host probes finish their ≤20 s and are discarded (traced).
8. **A backup aimed at the wrong device.** The FANUC job pointed at a
   camera used to produce a junk two-file backup that LOOKED like it ran
   (field bug, §7); now the no-`MD:` fallback fingerprints the login dir
   and refuses loudly, naming the fix ("set its device type to … in the
   library") — `ftpbackup.py:559-578`, verified both directions
   (`test_mtxbackup.py:376-383`, `test_keyencebackup.py:272-283`).

## 7. Traps paid for

- **`MATROX` vs `Matrox`.** The baked-in password was the wrong case; the
  camera's Samba compares exactly; every FIRST backup failed with
  STATUS_LOGON_FAILURE, and the only "working" pulls were riding an
  Explorer session a tech had opened by hand — which is why the bug looked
  intermittent. Live-verified against two cameras, then written into the
  source as the load-bearing comment at `mtxbackup.py:49-58`. Do not
  "normalize" those strings.
- **WinError 86/1326 on a workgroup laptop.** Windows sends its OWN
  machine name as the domain for a bare `mtxuser`; the camera rejects it.
  Fix: the connect ladder — staged credential + no-creds connect, plain
  creds, *server-qualified* `<ip>\mtxuser`, clear-stale-session + retry —
  each rung a distinct field failure (`mtxbackup.py:162-213`). The final
  refusal tells the tech the manual path that always works (Explorer,
  sign in, retry).
- **Error 1219: one credential set per server.** A tech's live Explorer
  session blocks a second programmatic login with different creds — so
  `smb_mount` first checks whether the share is already reachable and
  rides that session touching nothing (`mtxbackup.py:230-238`).
- **The junk backup that looked real.** A FANUC job pointed at a Matrox
  host "succeeded" with a couple of shell scripts and then choked on
  `da/` — a backup that LOOKS taken ("the backup didn't grab the camera
  data"). Now `_guard_not_camera` refuses with instructions (§6.8).
- **Credential-spraying, avoided by design.** The first Matrox discovery
  probed the camera login against every non-FANUC FTP host; the SMB era
  would have upgraded that to failed logons against every PC on the
  subnet. The EtherNet/IP identity gate exists so the share is touched
  only on hosts that already *are* cameras (`discover.py:432-440`; the
  spy test is the regression guard).
- **`ERRALL.LS` resets the data connection; reports hide from `nlst`.**
  Both live R-30iB behaviours. Naming therefore RETRs a hardcoded
  shortlist (LOGBOOK first) *by name* before trusting any listing
  (`discover.py:51-55,506-536`).
- **A 20-robot batch died on a settings rename race.** Every
  `start_backup` persisted the library root; 20 simultaneous calls raced
  the atomic settings replace on Windows and every job died before one
  file was pulled. Fix: write only on change, and a persist failure can
  never kill the backup (`api.py:3307-3318`); `settings._write` also
  retries the transient `PermissionError` (`test_settings.py`).
- **MAX_PATH, again, from the writer's side.** Deep camera trees (dated
  snapshot + `CAM1\Documents\Matrox Design Assistant\SavedImages\<date>\`
  + long filename **+ the `.part` suffix**) blow past 260 chars — the
  halfway-through "cannot find the path" failure. Every dest touch goes
  through `long_path` (`\\?\`), including `os.makedirs`
  (`ftpbackup.py:88-99,110-112`, `mtxbackup.py:267-291`; pinned
  `test_mtxbackup.py:112-127`). The reader-side sequel (photos vanishing
  from the index) is parsing.md §6's trap; same root cause, two layers.
- **`skipped` comes off the job, not the call site.** An earlier shape let
  the caller pass the skip list to the sidecar writer; forgetting turned a
  lossy pull into a clean-looking one. Now `_write_sidecars` reads
  `self._p["skipped"]` directly (`ftpbackup.py:301-310`, commit
  `85dd58d`), and the strip refuses the clean ✓ for done-with-skips
  (`jobs.js:269-281`).
- **The jobstrip that walked backwards.** Progress summed only
  still-active jobs, so the bar *dropped* every time a robot finished;
  and rebuilding the strip DOM every 500 ms tick made the details button a
  fresh element twice a second — clicks misfired. Now: run-scoped
  denominator (finished robots stay in it) and a build-once,
  update-in-place skeleton (`jobs.js:12-20`). The lesson generalizes to
  any polling UI.
- **Close guard fails open.** The backups-running confirmation returns
  True on any dialog failure — a GUI mid-teardown must never trap the
  user inside the app (`api.py:335-346`; `test_close_guard.py:53-61`).
- **PowerShell from a frozen exe.** Absolute exe path (PATH is unreliable
  in onefile), `-NoProfile -NonInteractive`, `CREATE_NO_WINDOW`, 5 s
  timeout, and any failure returns `[]` so the dialog falls back to the
  local /24 (`discover.py:106-136`).

## 8. Coverage

Counted 2026-08-01 from the tracked tests (all synthetic, all green on a
clean clone; the full-suite number is parsing.md §7's 701/0).

**Tracked, direct — 58 tests across 5 files.** `test_discover` (20:
backup-root detection, CIDR normalization, the FANUC/name-resolution fakes
incl. the R-30iB listing shape, Matrox via EIP with SMB open/closed/absent,
the spy-mount gentleness test, ListIdentity parsing, adapter parsing);
`test_mtxbackup` (16: probe/diagnose/name, scope, MAX_PATH both sides,
end-to-end incl. library registration, empty-share marker, multi-camera
layout, cancel, self-name + auto-link + rescan survival, the FANUC-refuses-
camera guard); `test_keyencebackup` (10: probe/diagnose, enumerate scope
±box, end-to-end incl. workspace manifest on snapshot AND mirror,
no-manifest-on-empty, multi-camera per-host workspaces, cancel, CWD-per-dir
enforced by the fake); `test_ftpbackup` (7: probe both ways, end-to-end,
cancel, the completion-marker double test, terminal vocabulary + run_id,
the api run-join stub test); `test_backuplog` (5: lifecycle + the
password-never-on-disk assertion, failed-specs filtering, retry
rejoin/replace/attempts, running-rows-never-clobbered, 20-run cap).

**Tracked, adjacent.** `test_close_guard` (4) pins §6.5's ask-first/
fail-open; `test_camera_link` (9) the auto-link key/fallback/ambiguity
rules; `test_library` the partial-never-latest chain `register_backup` feeds;
`test_settings` (2) the rename-race retry from §7; `test_sessions`,
`test_keyence_workspace`, `test_sim_export` cover the neighbours consuming
capture's output.

**What the engines' fakes deliberately model** (this is the suite's real
value): the CV-X pathful-RETR refusal, the R-30iB alphabetical listing with
buried reports, ERRALL's connection reset, controllers rooting at MD:, the
EIP reply byte layout, an SMB share as a plain dir. A regression against a
recorded live quirk fails offline, which is the only acceptable place for
it to fail first.

**The uncomfortable part — the UI flow has zero coverage.**
`docs/proposals/home-split.md` §5 recorded it 2026-07-28; re-verified
2026-08-01 by grep: **no probe or test anywhere exercises
`startLineBackup`, `promptSharedPassword`, `renderRowProgress`,
`reattachProgress`, `cancelAllBackups`, or calls the real `start_backup`
endpoint** — the only test matches are two comments in `test_mtxbackup`
saying "exactly what api's on_complete does", which is a hand-kept copy of
`api.py:3320-3345`, not a test of it. So: the engine that talks to a
production robot is well-pinned; the modal that collects the spec, the
endpoint glue that builds the job from the registry row (`_start_backup_job`
end to end, incl. `backuplog` wiring and the on_complete lambda), and the
strip a tech actually watches are held by nothing but care. home-split.md
already names the fix (a hidden-window flow probe with endpoints stubbed)
and calls it the gate for its phases 3–5; nothing has moved since.

**Also uncovered:** discovery's CV-X branch (`_scan_host`'s banner-gated
Keyence arm, `discover.py:419-430` — the probe it calls is tested, the
branch is not); `eip_list_identity`'s real socket path (broadcast-twice,
dedupe, timeout — `test_discover` injects above it) and `matrox_hosts`;
`local_ipv4`/`default_cidr` (touch the real network — properly untestable
offline, worth saying so); `diagnose_keyence`/`diagnose_camera` beyond the
happy path; `retry_failed_backups` at the endpoint level; every §6 row
marked "traced".

**Private-fixture side.** The four excluded pins (parsing.md §7) serve
capture too: the PARTIAL pin is this subsystem's pre-marker failure shape
preserved as real data (§4, measured today), driven by the excluded
`test_compare`'s skipped-category honesty. Note its conftest description
read "no .VA/.DG payload" — measurement says 53 A–F `.VA`/`.DG` files
present, everything ≥ G missing; the conftest comment was corrected this
pass (comment-only, in the untracked file).

## 9. Open questions

Found during this pass; written down, not fixed (ground rule: no feature
code changes). Evidence attached.

1. **CORRECTED THIS PASS — `ftpbackup.py`'s module docstring claimed
   "FR:/FRA: recursion is supported but off by default."** False since
   `f634c41` (2026-07-24) removed the never-run FR: walker and the
   `devices`/`recurse_fr` knobs (that commit updated `_enumerate`'s
   docstring but missed the module header). One line replaced with the
   truth + pointer; the only code-file touch this pass makes.
2. **The Matrox credential-rotation gap is real, but its paper trail is
   broken.** INVENTORY §E's ruling ends "Tracked in ROADMAP.md" — it is
   not: grep of ROADMAP.md for rotate/credential/mtxuser finds nothing
   (2026-08-01). The precise state of the gap, from code: the engine and
   registry accept overrides (`spec.user`/`spec.passwd`,
   `api.py:204-216`), and the batch flow *would* carry them — the edit
   modal can store `ftp.user` on any entry (`home.js:1787-1788,1929`) and
   the shared prompt's password rides every entry that has one
   (`home.js:1538-1550`) — but nothing documents this for cameras, the
   modal's own probe button sends the user with **no** password
   (`home.js:1810`), and the prompt is labeled "ftp password (shared)"
   for what is an SMB login. So a rotated site has an undocumented,
   half-working path, not a supported one. Either land the ROADMAP line
   INVENTORY promised or correct INVENTORY.
3. **Matrox throttle default is 0.0** where both FTP transports use 30 ms
   (`mtxbackup.py:311` vs `ftpbackup.py:425`, `keyencebackup.py:97`).
   Plausibly deliberate — SMB file copy off a Samba share vs a controller
   synthesizing files on GET — but no comment says so, and every other
   gentleness choice in these modules is annotated. **Assumed** deliberate;
   wants an owner ruling and a one-line comment either way.
4. **Concurrent same-robot jobs are unguarded** (§6.4). The engine
   tolerates every interleaving except same-second start (shared dated
   dir). Cheapest honest fix if ever wanted: refuse in
   `_start_backup_job` when an active job already has this host — but
   that's a behaviour decision (a tech may *want* to re-fire a stuck
   robot), so it's recorded, not made.
5. **A foreign partial is invisible by design** — only our own engine's
   `complete:false` marks one (`library.py:587-594`); a truncated tree
   copied in from elsewhere (or from the pre-marker era, like the PARTIAL
   pin) registers as a clean import and *can* become latest. The
   trade-off is sound (flagging all sidecar-less imports would cry wolf)
   but currently lives only in a code comment; a future health-scan check
   ("import looks truncated: no SUMMARY.DG / alphabet stops early") could
   catch the worst of it without new UI.
6. **`FR_MAX_FILES` outlived FR:.** Since `f634c41` it caps the flat MD:
   enumerate (`ftpbackup.py:41,554-556`); the name says FR:, the role is
   generic. Rename when the file is next touched functionally.
7. **`healthscan.py` imports `_ScanJob` from `discover.py`** for a
   progress/cancel base class, dragging ftplib/socket/EtherNet/IP imports
   into the scanning layer (INVENTORY §D's second inversion; the twin of
   `session.py ← ftpbackup.long_path`, parsing.md §8.8). Same candidate
   fix: a neutral job-base module, when a code pass owns it.
8. **Capture-test fixture IPs are `10.0.0.x`, not TEST-NET.** CLAUDE.md's
   firewall prescribes `192.0.2.x` for fixtures; `test_ftpbackup`,
   `test_mtxbackup`, `test_keyencebackup` and `test_discover` predate the
   convention and use RFC1918 `10.0.0.x` (identifying nothing —
   this pass's sweep confirms no term overlap with the real-plant list —
   but off-style; `test_backuplog` already uses TEST-NET). Cosmetic;
   align next time those files change functionally.
9. **`backup.json.tmp` uses a bare `Path.replace`** (`ftpbackup.py:297-299`)
   while every payload file goes through `long_path`. A dated dir deep
   enough to need `\\?\` for its files needs it for its marker too —
   unlikely for robots (shallow names), possible for a deep library root.
   Traced only; no field failure known. Worth folding into any future
   `long_path` consolidation (item 7's module).

---

## What this pass could not verify

The honest tail, per the template. No hardware was dialed — this
subsystem's own ground rule — so everything below is recorded-and-
fake-enforced, not re-proven live.

- **Every live-run (recorded) fact in §4**: the CV-X anonymous-FTP layout
  and 550-Bad-path behaviour, the Matrox SMB-only transport and
  credential casing, ERRALL's connection reset, reports hidden from
  `nlst`, the 21-camera EIP sweep, SavedImages rotating mid-pull. I
  verified each is recorded at the cited line, dated, and (where marked)
  enforced by an offline fake; re-proving any of them requires a plant
  floor.
- **FANUC controller tolerance of concurrent FTP sessions** — the
  one-connection rule is stated as precaution everywhere it appears; what
  a live R-30iB actually does with two simultaneous logins (refuse?
  degrade? affect the running program?) has never been measured, and
  §6.4's analysis of the double-fire case is therefore disk-side only.
- **Whether the 20 s ftplib timeout truly bounds a mid-transfer stall on
  FANUC's server** — by ftplib's API contract the socket timeout applies
  to data connections too, but no stalling controller has exercised it.
- **The listing caps** (5,000 enumerate / 20,000 walk) have never been
  hit outside tests as far as any record shows; behaviour at a real cap
  (a camera with >20k files under `setting/`) is by-construction only.
- **The EIP reply parser against non-Matrox industrial devices** — offsets
  are pinned synthetically and proven on Matrox replies; a device with an
  unusual encapsulation layout would be dropped as None (safe) but that
  discard path has no real-world corpus behind it.
- **The Credential Manager staging semantics under a rotated password** —
  `CRED_PERSIST_SESSION` scoping and the cleanup path are per Microsoft's
  documented behaviour plus our field use of the *default* credential;
  staging a non-default password (item 2's half-working path) has never
  been run.
- **`_watch_step`'s debounce against a real multi-minute Explorer copy** —
  the transition function is trivially correct (api.py:181-192) and the
  4 s cadence is field-tuned, but the end-to-end "one notification after
  the burst settles" claim rests on use, not a test.
- **The batch UI flow** — cannot be verified by reading, and currently
  isn't verified by anything else either (§8). Listed here too because
  it is the pass's largest single unknown: the code path a tech actually
  clicks between the password modal and `start_backup` has no safety net.
