# Parsing — the parser layer

*Subsystem doc #1. Written 2026-07-31 against `main` @ `0dca2d0`, clean tree.
Covers `src/backupviewer/parsers/` (28 files) plus `session.py`. Line-number
cites are against that revision and drift with edits; the anchor commit is the
reference.*

> **Template note.** This is the first subsystem doc and sets the shape:
> sections 1–8 below, closing with **What this pass could not verify**. That
> closing section is deliberate and every later subsystem doc should end the
> same way — an honest gap handed to the next session beats a confident guess.
> If a later doc's material demands a different shape, change it *and say so*,
> so the shape stays a decision rather than an accident.
>
> Division of labour: [CLAUDE.md](../../CLAUDE.md) owns the rules (layers,
> honesty contract, firewall, testing commands); [INVENTORY.md](../INVENTORY.md)
> owns per-file breadth (descriptions, line counts, subsystem assignments).
> This file holds only what neither has: ground truth and how it was verified,
> decisions and their alternatives, cross-file invariants, and paid-for traps.
> If something reads like a restatement of a function, it should be cut.

---

## 1. What it is

The parser layer turns a backup folder's files into the JSON-shaped models
every screen renders. `session.py` owns *which bytes to read* — one
`BackupSession` per opened backup: a recursive case-insensitive file index,
best-copy resolution, content-based file classification, backup-type
detection, and a lazy per-key parse cache. `parsers/` owns *what the bytes
mean* — pure functions, no file I/O, no state, one file format per module.
The boundary with neighbours: `api.py` glues session to parser and owns the
`{ok,data}` envelope; `healthscan.py` and `compare.py` are second and third
consumers of the same parsers; the frontend never parses robot files
(CLAUDE.md layer rule). Binary TP formats (`.TP`/`.PC`/`.MR`) are indexed and
counted but never decoded — only text formats are parsed, by design.

## 2. The files

Per-file descriptions live in the [INVENTORY map](../INVENTORY.md); this is
the structure the flat listing hides. The folder is *not* one subsystem — the
inventory assigns its 28 files to four: **backup parsing** (19: the engine
plus the robot-file leaves), **3D viewer** (4: `curpos`, `dcszones`,
`kinematics`, `roboguidedef`), **cameras** (4: `cvx_image`, `cvx_inspect`,
`mtx_portal`, `mtx_saved_image`), **program editor** (1: `ls_edit`).
`session.py` is backup parsing (also cameras). Use those assignments; don't
reinvent them.

Structurally the layer is two tiers:

- **Engines** — `common.py` (cp1252 read, `coerce_scalar`, FANUC dates,
  `MASKED`), `va.py` (the `.VA` record tokenizer + three body-shape readers),
  and `sysvars.py`'s display half (`record_tree`/`flatten`, which render *any*
  `VaRecord` as a tree or flat leaf map). Engines are where shared behaviour
  gets promoted; forking one is how the layer's worst bug happened (§6).
- **Leaves** — one module per format. `.VA`-family leaves (`registers`,
  `frames`, `macros`, `mastering`, `payloads`, `mhvalves`, `styles`,
  `dcszones`) sit on `va.py`; `.DG` leaves (`summary_dg`, `io_dg`, `dcs`,
  `curpos`) and `.LS` leaves (`ls_program`, `ls_edit`, `alarms`, `callgraph`)
  each carry their own line grammar; the camera leaves parse bytes
  (`cvx_image`, `cvx_inspect`) or non-backup text (`mtx_saved_image`, and
  `mtx_portal`, which parses live portal HTML fetched by the api layer).

New code goes where CLAUDE.md's quick reference says: a new file format is a
pure parser module here, a thin endpoint, and a `TAB_REQUIREMENTS` entry in
[`parsers/__init__.py`](../../src/backupviewer/parsers/__init__.py) — the
data-driven map of which files light which tab (`"*programs"`/`"*alarms"`/
`"*photos"` entries are special-cased by `BackupSession.manifest()`,
`session.py:385`).

Two deliberate structure decisions worth knowing before "fixing" them:

- **Two `.LS` readers coexist on purpose.** `ls_program.py` decodes cp1252
  with `errors="replace"` for *display* — lossy, any byte ≥ 0x80 that isn't
  clean cp1252 becomes U+FFFD and can never be encoded back. `ls_edit.py`
  decodes latin-1 for the *editor* — a 1:1 byte↔codepoint map, so a program
  round-trips byte-exact and an un-encodable edit fails loudly
  (`LsEncodeError` names the line, `ls_edit.py:26-31,50-64`). The cost is
  acknowledged: each carries its own body/POS regex set, so a format fix must
  be made twice. The alternative — one reader — was rejected because display
  wants replacement characters shown and editing cannot tolerate them.
- **`record_tree` lives in `sysvars.py`, not `va.py`.** CLAUDE.md's
  composition section cites it as the shared-engine model ("powers sysvars,
  KAREL vars *and* MH valves") — true, but the claim is about the *va.py
  engine family*, and the function itself sits in `sysvars.py:99`. Verified
  callers: the sysvars tab (`api.py:1523`), KAREL `.PC` variable views
  (`api.py:1255`), the MH valves full-config tree (`api.py:1544`), and — via
  `sysvars.flatten` — the PC-variable compare diff (`api.py:1258-1275`).
  `mhvalves.build_mhvalves` itself uses `va.parse_struct_fields`, not
  `record_tree`; the valves tab uses both paths.

## 3. The flow

```
backup folder (read-only evidence — nothing here ever writes into it)
   │
   BackupSession(root)                      session.py
   │   index: UPPER posix relpath -> Path   walked once via \\?\ paths (§6)
   │   by_name: basename -> best copy       priority: shallowest, then mdb/,
   │                                        then alphabetical (session.py:186)
   │   classify: .LS by CONTENT not name    /PROG -> program; report header
   │            KAREL = .VA + same-stem .VR twin, first section == stem
   │   detect: keyence / matrox / maintenance data / MD / all-of-the-above
   │
   s.text(name) ── parsers.* (pure) ──> dict   cached via s.cached(key, build)
   │                                           per-key lock: two concurrent JS
   │                                           calls parse once (session.py:219)
   │
   api.py endpoint  ->  {ok,data} envelope  ->  web/js tab
```

What's pure vs stateful: every `parsers/` function is pure (text or bytes in,
JSON-able dict out — no I/O, no globals). All state lives in `BackupSession`:
the index built once at open, and the `_cache` that lives for the session.
Nothing heavy parses at open; each tab's model is built on first request
(`session.py:12-14`).

Three consumers share the parsers, two of them share cache state:

- **`api.py`** — cache keys are either plain (`"io"`, `"frames"`,
  `"dcszones"`, `"sysvar_index"`, `"mhvalves"`, `"photos"`) or parameterized
  (`"program:NAME"`, `"alarms:NAME"`, `"dcs:NAME"`, `"registers:kind"`,
  `"karel:STEM"`).
- **`healthscan.py`** — its `_RobotData` (healthscan.py:126) wraps a
  `BackupSession` per scanned robot and uses `hs_*`-prefixed keys so a scan
  never poisons a viewer cache — with one deliberate exception: `"progtext"`
  is shared with api.py *by name* ("same key/shape api.py uses",
  healthscan.py:184). That is a cross-module contract: change the shape under
  that key in either place and the other silently gets the old/new shape.
- **`compare.py` / `search.py`** — reached through api helpers on two
  sessions (compare keys the second one `side="b"`; the trailing-`side`
  parameter-order rule is CLAUDE.md's, pinned by `tests/test_sessions.py`).
  Parsers themselves are side-blind — purity is what makes the compare
  feature free.

## 4. Domain truths

The crown-jewel section. Everything below is a fact about FANUC/Keyence/
Matrox artifacts that cost real verification effort, with *how it was
verified* — or an honest **assumed**. The evidence tags:

- **pendant-paired** — value matched against pendant/controller output for
  the same robot, same data.
- **corpus-measured** — measured across N real files; N stated.
- **live-run 2026-07-31** — re-verified today by command against the private
  fixture tree (counts printed, identifiers not).
- **assumed** — plausible, in the code, *not* verified; UI must show `?` +
  raw per CLAUDE.md's parse-what-you-can-prove rule.

### Files as a whole

| Fact | Evidence |
|---|---|
| One backup mixes line endings (SUMMARY.DG LF; .VA/.LS CRLF) → always `splitlines()`. Content is cp1252-ish; decode with replacement so no file ever fails to read | verified against a real MD backup, recorded `common.py:1-8` |
| `'********'` = masked value; `'Uninitialized'` = no value | same header; meaning of masked refined below |
| Two-digit years pivot at 70 (`97` → 1997, `26` → 2026) | `common.py:53-61`; assumed cutoff — no pre-1990 backup has been seen to prove the pivot placement |

### `.LS` programs (both readers)

| Fact | Evidence |
|---|---|
| Body line = 4-wide right-justified number + `:` + separator + text + `' ;'`; separator is 2 spaces EXCEPT motion lines (`J/L/C/A` + space) which butt against the colon; long statements wrap with `    :  ` continuations, `';'` on the last physical line; everything CRLF | **corpus-measured**: 6,478 real programs / 378k body lines, zero counter-examples (`ls_edit.py:7-17`) |
| Position values print with 3 decimals | **corpus-measured**: unanimous across 30k values (`ls_edit.py:265`) |
| `FILE_NAME` in `/ATTR` is vestigial — it disagrees with its own file in the wild | **corpus-measured**: 90 of 400 sampled programs (`ls_edit.py:193-199`); why rename only rewrites `/PROG` |
| A masked `********` point is *placed logically but never initialized with data*; typing a value initializes it — so the editor treats masked fields as editable | domain call recorded `ls_edit.py:271-273`; ground-truthed during the editor build (pendant behaviour), not re-provable from files alone |
| `.LS` is a program ⇔ content starts `/PROG`; otherwise a report dump whose first line is `<NAME>.LS  Robot Name <host> <date>` — the name tells you nothing | `session.py:9-11,229-252`; how ERRALL.LS et al. are told apart from TP source |
| A KAREL program = binary `<stem>.VR` + text `<stem>.VA` twin whose first record's section equals the stem (excludes shared dumps like NUMREG.VA) | `session.py:254-276` |
| `!` lines are comments, `//` lines are remarked-out instructions; the robot executes **neither**, and `//CALL` ships fleet-wide as a deliberate standard | field fact, `callgraph.py:41-46`; counting `//CALL` produced false broken-call flags (§6) |

### `.VA` dumps (the `va.py` engine)

| Fact | Evidence |
|---|---|
| Three body shapes cover every `.VA` we read: scalar arrays, position arrays (cartesian or joint), struct fields | `va.py:1-22`; held across the corpus so far — a fourth shape would land in `record_tree`'s verbatim branch, visible not lost |
| A trailing quoted string on a scalar-array line is the COMMENT when something precedes it (`[1] = 10 'Spot Count G1'`) but the VALUE when alone (`[4] = 'STYLE04'` — string arrays dump quoted) | `va.py:99-123`; mis-reading this swaps values and comments in string registers |
| KAREL struct fields dump *plain* field names (no `$`), so the `$` in `Field:` lines must be optional | `va.py:36-39`, `sysvars.py:35-41` — the same lesson threaded through both engines |
| The `[*SYSTEM*]` section tag — not the filename — is a system variable's identity; the controller scatters them across SY*.VA chunks and odd names (CELLIO, DCSIOC, DCSPOS, TWLOGVAR) | `sysvars.py:1-11`; **live-run 2026-07-31**: on a real MD backup, 190 `.VA` files total, 24 carry `[*SYSTEM*]` records; SYSTEM.VA alone holds 744, the merge yields 978 |
| Every `[*SYSTEM*]` `$`-name is unique across a whole dump — nothing to collide when merging | claimed `sysvars.py:58-59`; **live-run 2026-07-31** confirmed (978 records, 978 unique names) on that one robot; other loadouts unproven, and the first-record-wins guard stays (`api.py:1500-1505`) |
| `$STYLE_NAME/$STYLE_COMNT/$STYLE_ENAB` live in CELLIO.VA on some cells and SYSTEM.VA on others; unused slots hold `'********...'` | `styles.py:1-4` |
| Payloads: FANUC's *default* mass is 100 kg, so mass alone is not evidence a schedule is real (needs comment or non-zero CG/inertia); `-9999` is the unset sentinel for groups the robot doesn't have | `payloads.py:41-47`; pinned by `tests/test_payloads.py` |

### MH valves (`MHGRIPDT.VA`)

| Fact | Evidence |
|---|---|
| A valve's `*_SN` field is NOT a DI/DO number — it is a 1-based index into one of four signal tables in the same file (VALVE/PARTP/CLAMP/VMADE); the table entry holds name, type (`_T`: 1=DI, 2=DO) and the real number (`_I`) | `mhvalves.py:13-24`; **pendant-paired** on a real bin-picker cell's dump: two valves' resolved DO/DI numbers matched the pendant (`mhvalves.py:26-28`) |
| Index 0 / an empty table slot is a *controller default*, not a wired signal — `VACMADE_SN[1]=1` sits on every valve even with no vacuum wired, and `GRIP_VSENSOR` is 1 on every valve | `mhvalves.py:16-19`; the phantom-vacuum bug (§6) |
| One clamp index = one photo head (open + closed pair, interleaved per head) | `mhvalves.py:23,111-121`; **assumed** from table structure + pendant menu shape, not independently re-verified |

### DCS (`dcszones.py` / `dcs.py`) — the model for marking verified vs not

| Fact | Evidence |
|---|---|
| `$MODE` 0 ↔ keep-in diagonal ("Working zone(Diagnal)"), 1 ↔ keep-out diagonal (both pendant vocabularies), 3 ↔ polygon keep-out ("Restricted zone(Lines)") | **pendant-paired** — each mapping paired with the same robot's verify-report text (`dcszones.py:12-17`) |
| `$MODE` 2: **never seen on a real controller** — method reads `?` + a vertex-count heuristic | `dcszones.py:16-17`, ROADMAP DCS lane; the honest gap that defines this repo's style |
| `$STOP_TYP` 0/1/2 ↔ Power-Off / Controlled ("Stop Category 1") / Not stop; newer pendants print the "Stop Category" wording | **pendant-paired** (`dcszones.py:18-20`) |
| `$MODEL_NUM` legend: 0=Disable, −1=Robot, −2=Tool, positive n = user model n | printed by the pendant itself in the report (`dcszones.py:21-23`) |
| `$DCSS_MODEL` elements: `$SHAPE` 1=Point 2=Line_seg, `$LINK_NO` 99=FacePlate, `$LINK_TYPE` 1=NORMAL | **pendant-paired** (`dcszones.py:24-27`) |
| `$SIZE[2]` is nonzero on real elements but no pendant report ever prints it — passed through raw, **meaning unverified** | `dcszones.py:27-29,444-446` |
| `$NUM_VTX` stays at its factory 8 on Diagonal zones — only trusted in Lines mode; diagonal geometry is always the 2 corner points | `dcszones.py:31-32,232-247` |
| `$DCSS_TUIRO`/`$DCSS_TUIZN`: **unknown semantics**, not handled; none seen configured with geometry | `dcszones.py:32-33` |
| The pendant has two method-text vocabularies, including FANUC's own "Diagnal" spelling — both parsed, verify text wins over `$MODE` when present | `dcszones.py:48-51,203-229` |
| Without DCSPOS.VA, zones rebuilt from the verify report's Point 1/Point 2 are frame-less (the DCS user frame's rotation isn't in the report) and flagged `approx` | `dcszones.py:381-418` — lossy-but-drawable, said out loud |

### Kinematics (`kinematics.py` / `roboguidedef.py` / `curpos.py`)

| Fact | Evidence |
|---|---|
| The FK chain (home placements from `.def`, joint motion `T·Rz·T⁻¹`, CAD→world ZeroOffset shift, NegDirection flip, ParallelLink J3 slaved to J2) | **pendant-paired**: brute-forced then validated against controllers' own CURPOS.DG — 0.13 mm / 0.005° on the Roboguide testbed, <0.15 mm and ≤0.03° across five robot families (`kinematics.py:3-5`, `roboguidedef.py:16-19`) |
| A `.def` is *not* Denavit-Hartenberg — `OffsetCADToAxis` is the absolute home placement of each joint frame; rotation about local Z | `roboguidedef.py:4-10` |
| `NegDirection` sits on J2 of every FANUC arm parsed so far | `roboguidedef.py:11-12`; corpus observation, not a spec guarantee |
| The robot type name is NOT in the `.def` XML; the filename carries it | `roboguidedef.py:20-22` |
| `-IF` dress variants carry an adapter plate the plain library `.def` omits: +23.0 mm (two families), +10.06 mm (one) — recovered per robot by `measure_flange` from the backup's own CURPOS + taught tool, trusted only when the residual is a pure flange-Z shift (|xy| < 1.5 mm, orientation < 0.1°) | `kinematics.py:15-23,125-148`; the trust gate is the point — anything else means "do not draw the pose" |
| CURPOS.DG's active tool number sits on the line directly above each world block | `curpos.py:64-67`; positional pairing, no labeled association exists in the file |

### Cameras

| Fact | Evidence |
|---|---|
| CV-X `.bmp` files under `cv-x/setting/` are two kinds, told apart by bit depth: 8-bpp palettised = intensity photo; 24-bpp = packed 15-bit **height** data, `H = (G << 7) \| (R << 4) \| B`, all-zero pixel = no data | **corpus-measured**: 157 files, six cameras, two plants — nothing else appears (`cvx_image.py:4-7`) |
| How the packing was established (it is documented nowhere): G's weight pinned at 128 by watching it step exactly when the low field rolls over 127→0 on smooth surfaces; that ordering scores ~4× smoother across scanlines than any other arrangement; R ≤ 7 and B ≤ 15 corroborate the field widths | `cvx_image.py:25-30` — kept because it is the *method*, reusable next time a vendor format needs cracking |
| A true height of 0 encodes identically to "no return" — unreadable, a format limit; real cameras sit their range at ~4,000–29,000 counts, clear of it | `cvx_image.py:16-19` |
| A CV-X carries no readable controller name over FTP (banner = model only; env.dat = paths, no identity); the inspection *program names* are the camera's own account of itself | `cvx_inspect.py:3-9` |
| `inspect.dat` holds the program name at 0x4C and a byte-for-byte echo at 0x398; the two agreed in **154/154** real files (13 cameras, 2026-07-25) — so the parser *requires* agreement, making the read self-validating against mis-seeks and impostor files (nearby fields hold localized factory defaults like `Neu115` a looser scan would grab) | `cvx_inspect.py:11-23` |
| One camera name out of many drifting program names: most-frequent wins, ties break to the lowest program slot (oldest, plainest); verified to pick right for all 13 sample cameras | `cvx_inspect.py:66-88` |
| Matrox saved photos come as jpg/png/txt triples; the `.txt` sidecar's values contain colons (timestamps, MACs) so keys split on the FIRST colon only; a colon-less line starts a section | `mtx_saved_image.py:3-33` |
| Older Matrox portals write literal DesignAssistant links; DA 9.x never does — each project row carries a `prj-name` attribute (unquoted in the wild) and the portal builds the URL in JS, so the parser builds the same one | `mtx_portal.py:6-12` |

### `.DG` reports

| Fact | Evidence |
|---|---|
| SUMMARY.DG is pseudo-HTML (`<H2><A NAME=…>` sections, `<PRE>` bodies); section bodies use `…::` mode markers | `summary_dg.py:1-9` |
| Backups without IOCONFIG/IOSTATE (all-of-the-above, maintenance data) carry the same signal tables — same line formats, same `::` marker — inside SUMMARY.DG's I/O sections | `summary_dg.py:65-79`; the io-fallback path |
| SUMMARY.DG *truncates the first character* of the customization string; GMWIZLOG.DT carries it untruncated — a controller-side bug, theirs not ours | `gmwizlog.py:14-16` |
| IOSTATE state columns float or butt against the bracket; FLG rows pack TWO signals per line — pre-split on 2+ spaces followed by a full `TYPE[ n] STATE` entry, and the strict state-token rule is what keeps comments like `UserGI[26]Bit 1` from reading as a second column | `io_dg.py:9-19` |
| `.DG` long type names vs pendant short names (`DIN`→`DI`, `FLG`→`F`); the viewer shows short names exclusively, and search aliases queries the same way | `io_dg.py:37-48`, `search.py:15-16` |
| Alarm rows are quote-delimited (`seq " datetime " code+msg " cause " severity+flags" act`); anything that doesn't decode lands under `unparsed`, never raised, never dropped | `alarms.py:1-9,30-42` |
| DCS reports print a duplicated "input size" line under CIP safety, and leading numbers in raw sections are pendant *menu indices*, not data | `dcs.py:56-61` |

### Assumed-but-unverified maps (say so in UI)

- `macros.ASSIGN_TYPE_NAMES` (`macros.py:11-23`) — ten numeric codes mapped to
  pendant assignment kinds, header says "Observed/pendant convention" but
  *which* of the ten were actually observed is unrecorded. Unknown codes fall
  back to the raw number, so the honesty rule holds mechanically; the map
  itself is not per-value ground-truthed the way DCS `$MODE` is. (§8)
- The `-9999` payload sentinel is inferred from groups the robot doesn't
  have; no FANUC document confirms it.
- `dcszones` unknown-`$MODE` heuristic (any vertex beyond the first two set →
  treat as polygon, `dcszones.py:243-247`) is a guess and labels itself
  `method_source: "heuristic"`.

## 5. Invariants

What must stay true, and what breaks if it doesn't:

1. **Parsers are pure.** Text/bytes in, JSON-able out — no file I/O, no
   state. This is what makes them unit-testable without a backup, reusable by
   healthscan/compare/search, and side-blind for the compare feature. An
   impure parser breaks all three at once. (`session.py` and the api layer
   own all I/O.)
2. **Presence = existence; the session never invents.** The index is a walk
   of what's on disk; `manifest()` claims a tab only when the files (or
   content-classified programs/alarms/photos) are there. When a bound trips
   it says so: `truncated_scan` on the 20,000-file cap (`session.py:35,171`),
   `stats["truncated"]` on `find_backup_roots`' cap — callers must surface
   these, not swallow them.
3. **Best-copy resolution is deterministic**: shallowest path, then the
   `mdb/` controller-dump dir, then alphabetical (`session.py:186-192`).
   Reports are classified on the best copy only — listing duplicates would
   double every alarm file (`session.py:229-232`). Change the priority and
   which SUMMARY.DG a hierarchical backup shows silently changes.
4. **Every `Path` the session hands out carries the `\\?\` prefix**; the walk
   root is the extended-length form while `self.root` stays as-spelled (it is
   the session id and display path; `root_key` is the canonical form for
   "same backup reached another way", `session.py:149-161,398-410`). Strip
   the prefix anywhere and deep Matrox image paths silently fail `is_file()`
   again (§6).
5. **`s.cached` is per-key locked and lives for the session** — two
   concurrent JS calls parse once; nothing invalidates (a backup is immutable
   evidence). Key namespaces: api's plain/parameterized keys, healthscan's
   `hs_*` — and the ONE deliberately shared key, `"progtext"`
   (healthscan.py:184). Its value shape is a two-consumer contract.
6. **The `[*SYSTEM*]` tag is identity, filename is provenance.** The merge
   keeps `source` per record as a tag; the browser sorts by `$`-name, never
   groups by file (`api.py:1506-1508`). Grouping by file would shatter the
   pendant's mental model of one variable list.
7. **Engines get extended, never forked.** The one recorded fork
   (`dcszones`' private `_value` replacing `coerce_scalar`) produced a
   safety-relevant wrong answer (§6). The `dcszones.py:63-71` comment now
   says "do NOT re-implement these here" — that instruction is the invariant.
8. **`emit()` renumbers, refs preserve bytes.** In `ls_edit`, `{"ref": i}`
   passes a record through byte-exact except the 4-char number field;
   `{"text": s}` emits the corpus-measured canonical form. Splices
   (`apply_attrs`/`apply_positions`) replace only the value token and
   preserve every surrounding byte — derived sizes and timestamps are the
   controller's numbers, "not ours to invent" (`ls_edit.py:224-225`). This is
   the editor's trust anchor; the round-trip fuzz over the 6,478-program
   corpus is its proof.
9. **Unverified maps fall back to `?` + raw** — `_MODES`/`_SHAPES`/
   `_LINK_TYPES` misses, unknown assign types, unknown stop types all render
   the raw value, never a guess. Extending a map requires pendant pairing
   (§4), not plausibility.
10. **The JS `fk.js` twin must equal `chain_frames`.** The math is
    duplicated in JS *deliberately* (posing the arm per frame in Python
    would mean a bridge call per orbit tick); the docstring claims "the
    probe holds them equal" — **currently false**, see §8.

## 6. Traps paid for

The bug, the cause, and what prevents recurrence. These are the expensive
lessons; do not re-learn them.

- **A disabled safety zone read back as ENABLED.** `dcszones` had a private
  `_value` that returned literal text for `'Uninitialized'` and `'FALSE'` —
  both truthy strings — so `bool(rec.get("ENABLE"))` was True for a zone that
  was explicitly disabled, and an uninitialized `$UFRM_NUM` crashed `int()`.
  Worst-possible failure for a safety screen. Fix: the shared
  `coerce_scalar` (`dcszones.py:63-71`); prevention: the fork-ban comment +
  `tests/test_dcszones.py`'s disabled/uninit honesty tests.
- **Phantom vacuums on every valve.** The old MH view resolved controller
  *defaults* as live signals: `VACMADE_SN[1]=1` exists on every valve, and
  keying "vacuum" off `GRIP_VSENSOR` (1 everywhere) made every gripper claim
  a vacuum. Rule now: a signal is real only when its resolved table number
  is > 0 (`mhvalves.py:44-52`); prevention: the no-phantom-vacuum guard in
  `tests/test_mhvalves.py`.
- **`//CALL` false-flags.** Counting remarked-out `//CALL` lines as edges
  made the health scan flag "broken calls" that never run and mark orphans
  reachable. `//` ships fleet-wide as a deliberate standard. Fix:
  `callgraph.py:41-46` skips both `!` and `//`; prevention: healthscan tests
  cover the demotion.
- **"No photos" with the photos on disk.** The index walked the plain path
  while the backup writer had landed deep Matrox images via `\\?\`; past 260
  chars, `is_file()` is a failed stat = False, and files silently vanished
  from the index. Fix: walk `\\?\` + `abspath` first (`.\..` must resolve
  before prefixing — `\\?\` skips Win32 normalization), `session.py:149-161`.
  This is also why `session.py` imports `long_path` from `ftpbackup.py` — a
  known layer inversion (INVENTORY §D), tolerated until the helper finds a
  neutral home.
- **Lossy decode almost ate the editor.** cp1252-with-replacement turns
  unknown bytes into U+FFFD, which cannot encode back — fine for display,
  corruption for export. Hence latin-1 in `ls_edit` and the loud
  `LsEncodeError` with line number instead of silent damage
  (`ls_edit.py:26-31,50-64`).
- **A height image looks *almost* like a photo.** Open a CV-X 24-bpp file in
  Paint and green-as-high-byte makes a washed-out but legible-looking image —
  exactly wrong enough to mislead techs into trusting it as a photo
  (`cvx_image.py:20-23`). The decoder renders height as a stretched ramp with
  transparent no-data instead.
- **The plausible-name grab.** `inspect.dat` carries localized factory
  defaults (`Neu115`, `Nouveau115`) near the real name — a "first text run"
  scan would have named cameras after UI slots. The dual-copy agreement
  requirement exists to make that structurally impossible
  (`cvx_inspect.py:17-23`).
- **String-array values read as comments.** The trailing-quote rule in
  `parse_scalar_array` (§4) is the fix for STRREG/style values swapping into
  the comment column.
- **Sections vs `-m ""`** — the verify command is spelled
  `-m "probe or not probe"` because PowerShell eats an empty `-m` argument
  before pytest sees it (CLAUDE.md, Verifying the app). Parser tests are part
  of that suite; use the spelled form.

## 7. Coverage

Verified 2026-07-31: `python -m pytest tests -m "probe or not probe"` →
**620 passed, 74 skipped, 3:31** on this machine.

**Tracked, direct** (run on any clone) — 115 tests across 10 files:
`test_ls_edit` (31, incl. the byte-round-trip engine), `test_cvx_image` (29),
`test_dcszones` (12), `test_kinematics` (11, synthetic `.def`; FK
expectations hand-derived), `test_cvx_inspect` (10), `test_sysvar_merge` (9),
`test_magnet` (5), `test_mhvalves` (4), `test_label_xref` (2),
`test_payloads` (2).

**Tracked, indirect**: `test_healthscan` (25) drives ten parsers (`alarms`,
`callgraph`, `dcs`, `ls_program`, `macros`, `mastering`, `payloads`,
`registers`, `styles`, `summary_dg`) with synthetic texts through a fake
session; `test_discover` covers `looks_like_backup`/`find_backup_roots`;
`test_ftpbackup`/`test_keyencebackup`/`test_mtxbackup`/`test_cvx_image`
construct real `BackupSession`s over synthetic trees and assert manifests;
`test_mtx_remote` covers `mtx_portal.find_da_pages`; `test_modeldb` re-uses
the `.def` chain; `test_mtxbackup`'s self-naming path exercises
`mtx_saved_image.parse_saved_image`.

**Git-excluded, direct** — 81 tests across 14 files that assert against the
private real-backup fixture (and its real robot names and F-numbers — which
is *why* they are excluded; correct firewalling, per CLAUDE.md):
`test_alarms` (3), `test_dcs` (12), `test_frames` (6), `test_io` (6),
`test_io_fallback` (4, incl. the FLG column split), `test_macros` (1),
`test_programs` (7), `test_registers` (3), `test_session` (6),
`test_session_formats` (6), `test_summary` (7), `test_sysvars` (5, incl.
`record_tree` on KAREL structs), `test_va_tokenizer` (4, one synthetic),
`test_v02_parsers` (11: callgraph, gmwizlog, mastering, styles, pendant
names, search).

**What a clean clone therefore does *not* verify**: the parsers' deepest
coverage — real controller dumps through `summary_dg`, `io_dg`, `dcs`
section classification, `frames`, `registers`, `alarms`, `gmwizlog`,
`mastering`, `styles`, the `sysvars` display engine, and `BackupSession`'s
index/classification against the three real backup formats. A clone sees
those parsers tested only as far as healthscan's synthetic texts exercise
them.

**Worse, and newly measured (§8): those 74 excluded+fixture tests currently
skip on this machine too** — the conftest points at three format subtrees
(`MD/`, `maint_data/`, `all_above/`) that no longer exist since the sample
tree was reorganized into a plant-shaped library (~2026-07-20). Skip reason
verified today: every fixture test reports "MD sample backup not present".
Until the fixture trees are restored or the conftest repointed, the
real-backup parser coverage runs **nowhere**.

**No coverage at all** (tracked or excluded): `mtx_saved_image.
group_photo_files`/`photo_record` (the photos-tab grouping, `api.py:1987`),
`curpos.parse_tool_frames`' section-end edge cases beyond what
`test_kinematics` touches, and the `fk.js` JS twin (§8). The `parsers/`
docstring-level claims from corpora (6,478 programs, 157 BMPs, 154
inspect.dat) are records of past measurement, not repeatable tests — the
corpora are not in the repo.

## 8. Open questions

Found during this pass, written down instead of fixed (ground rule: a doc
pass changes no code). Evidence attached so the next session doesn't have to
re-derive it.

1. **The private-fixture suite is silently skipping.** `tests/conftest.py`
   (git-excluded) expects `SampleBackup/MD`, `SampleBackup/maint_data`,
   `SampleBackup/all_above`; the actual `SampleBackup/` now holds a single
   plant tree and none of those subfolders. All 74 fixture-dependent tests
   skip ("MD sample backup not present"), and because `_dir_fixture` uses
   `pytest.skip`, the headline stays green. The suite last exercised real
   controller dumps before the ~2026-07-20 reorg. Needs a decision: rebuild
   the three format trees (from any robot + a maintenance-data pull), or
   repoint the conftest at backups inside the plant tree. Until then,
   INVENTORY's description of these tests as the parsers' deepest coverage
   is aspirational.
2. **`kinematics.py:27` claims "the probe holds them equal" (fk.js ↔
   `chain_frames`) — no probe does.** `grep -i fk tests/` matches only
   `test_kinematics.py`'s own docstring; `ui_probe.py` has no 3D checks at
   all. The JS twin is currently unpinned; a drift would mis-pose the 3D arm
   with every unit test green. (The stale docstring is also a §C-style
   prose-vs-code mismatch.)
3. **`$MODE = 2`** remains unmapped — never seen on a real controller
   (ROADMAP, DCS lane). Shows `?` + heuristic, as designed. Needs one backup
   whose pendant pairs it.
4. **Newer-vocabulary Lines vertex table renders raw** — the
   "Working/Restricted zone" pendant generation prints its vertex table in a
   layout `dcs.py`'s pos-table parser doesn't structure (correct but
   unstyled; ROADMAP). Teach `_parse_list_detail`'s pos-table state machine
   that shape when a backup needs it.
5. **`$SIZE[2]` on DCS model elements and `$DCSS_TUIRO`/`$DCSS_TUIZN`**:
   semantics unknown (§4). Passed through raw / not handled.
6. **`macros.ASSIGN_TYPE_NAMES` verification status is unrecorded** — which
   of the ten codes were pendant-observed vs pattern-completed is lost. Next
   time a pendant is at hand, pair the table and annotate per-value like
   `dcszones` does.
7. **`parsers/__init__.py:1-3` docstring is stale** ("All parsers are pure
   functions text -> JSON-serializable dicts") — `cvx_image`/`cvx_inspect`
   parse *bytes* and `mtx_portal` parses live portal HTML. Purity holds;
   "text" doesn't. Already flagged in INVENTORY §C; recorded here because
   this file is now the layer's reference.
8. **`session.py` ← `ftpbackup.long_path` layer inversion** (INVENTORY §D):
   parsing imports from capture for one path helper. Candidate fix is moving
   `long_path` to a neutral module (`common.py` is the obvious seam — it
   already owns "how to read files safely"), but that touches capture-side
   callers, so it waits for a code pass.
9. **`mtx_saved_image`'s grouping half has no tests** (§7). The parse half
   is pinned indirectly; `group_photo_files`/`photo_record` — which decide
   what the photos tab shows — are not.

---

## What this pass could not verify

The honest tail, per the template. Later subsystem docs: end with yours.

- **The corpus-measured claims** (6,478 `.LS` programs / 378k lines; 30k
  position values; 157 CV-X BMPs; 154 `inspect.dat` files; 400-program
  FILE_NAME sample) — the corpora live outside the repo. I verified the
  claims are *recorded* at the cited lines and that the current tests enforce
  their consequences, not the measurements themselves.
- **Pendant pairings** ($MODE/$STOP_TYP/model legends, the MH valve DO/DI
  resolutions, the masked-point editing semantics) — verified as recorded +
  consistent with the honesty pattern (`?` fallbacks in code), but I had no
  pendant. Re-pairing them needs plant access.
- **The kinematics accuracy numbers** (0.13 mm / 0.005°, five families) —
  the tracked tests prove the chain math against hand-derived synthetic
  expectations and two flange cases; the against-real-controller residuals
  are recorded results, re-provable only against real CURPOS dumps (currently
  blocked by open question 1).
- **`[*SYSTEM*]` name uniqueness** — proven today on one robot (978/978);
  claimed "in practice" for all. Other software loadouts unchecked.
- **The two-digit-year pivot at 70** — no backup old enough (or clock-wrong
  enough) to exercise the 19xx branch was available.
- **Behaviour on a genuinely malformed `.VA`/`.DG`** (truncated mid-record,
  binary garbage) — the parsers are written tolerant (skip/unparsed/verbatim
  branches) and `alarms`/`summary_dg` state it, but no test feeds them
  garbage systematically; tolerance is by construction, not by proof.
