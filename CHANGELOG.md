# Changelog

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
