# 3D viewer — the solve, the table, the projection, the zones

*Subsystem doc #3. Written 2026-08-01 against `main` @ `0372b09`, clean tree
(plus this pass's own two comment corrections in `view3d.js` and
`dcszones.py`, §9 item 1 — both 1-for-1 line swaps, so cites into those
files hold). Line-number cites are against that revision and drift with
edits; the anchor commit is the reference.*

Covers: src/backupviewer/kinematics_builtin.py, src/backupviewer/modeldb.py,
src/backupviewer/parsers/curpos.py, src/backupviewer/parsers/dcszones.py,
src/backupviewer/parsers/kinematics.py, src/backupviewer/parsers/program_path.py,
src/backupviewer/parsers/roboguidedef.py,
src/backupviewer/web/js/components/fk.js, src/backupviewer/web/js/components/proj3d.js,
src/backupviewer/web/js/tabs/dcs.js, src/backupviewer/web/js/tabs/view3d.js
(11 files)

Not covered, from the inventory's 16-file "3D viewer" group: `robot
modelas/_re/RMD-FORMAT.md`, `robot modelas/_re/rmd.py`, `robot
modelas/_re/rmd_assemble.py`, `robot modelas/_re/rmd_capsulefit.py`, `robot
modelas/_re/rmd_png3.py`, `robot modelas/_re/rmd_validate.py` (6 files) —
git-excluded reverse-engineering tooling that does not ship and does not
exist on a clean clone; documenting it as part of the app would be a lie of
placement. It gets a provenance paragraph in §2 and nothing more.

> **Template note.** backup-capture.md's ten-section shape is kept,
> **including §6 Failure modes** — with a refinement to its keep-it-for-
> stateful rule. Half this subsystem is pure math (the FK chain, the
> projection) and would drop §6 under that rule as written. But the doc
> covers the *viewer*, and the viewer's defining job is rendering honestly
> under missing or contradictory evidence: the pose pipeline needs five
> independent pieces (a type string, a matched chain, CURPOS, FRAME.DG, a
> world TCP) and any subset can be absent or disagree. What the screen
> claims in each case *is the product* — same "what does the world's
> breakage look like afterwards" content as a capture job's death, spatial
> rather than temporal. So the refined rule for doc #4+: keep §6 when the
> subsystem must degrade honestly under missing/contradictory input, not
> only when it has jobs that die mid-flight.
>
> Evidence tags: **pendant-paired** carries its original meaning again —
> this is the subsystem it was invented for (values matched against a real
> controller's own output). **live-run \<date\> (recorded)** = discovered
> against real equipment on a stated date, recorded in code/history;
> **live-run 2026-08-01 (re-measured)** = re-proven today against the
> pinned private fixtures (numbers printed, identifiers not).
> **corpus-measured** and **assumed** keep their meanings. Where a fact is
> enforced by a test rather than only remembered in a comment, the
> evidence column says so.
>
> Division of labour: CLAUDE.md owns the rules; INVENTORY.md owns per-file
> breadth; **parsing.md §4 already owns the DCS pendant-paired maps**
> ($MODE/$STOP_TYP/$MODEL_NUM/$DCSS_MODEL legends, $SIZE[2], TUIRO/TUIZN)
> and the kinematics/curpos parser facts — those are linked, not restated.
> (The scope brief said `dcszones` had 15 hits in backup-capture.md; checked
> 2026-08-01 — it has zero there. All prior coverage lives in parsing.md.)

---

> **2026-08-20c — playback.** The tab has an animation loop now, which makes
> one sentence in §6 that stood since the doc was written **false**: it is no
> longer true that "the tab renders statically per draw call". That row is
> rewritten. The loop is time-driven off `Date.now()` through either
> `requestAnimationFrame` or `setTimeout` — the probe environment has no rAF
> at all and the fallback is the path it exercises, deliberately. Per frame it
> rewrites exactly two nodes: the `v3-l-arm` group and a trailing
> `v3-ovl-live` overlay group. Nothing recomputes the projector, the viewBox,
> the fit or the painter's sort.

> **2026-08-20b — the inverse solve.** `kinematics.py` gained a damped
> least-squares solver (add-only; not one line above `measure_flange` moved,
> so `ui_fk_probe` and `test_kinematics` hold by construction) and the arm now
> poses at whichever program step you select. §4 has its own table for it. The
> one thing worth carrying in your head: the solver's honesty gate checks the
> POSE, and a wrong **branch** reproduces the pose perfectly — so a solved
> posture is labelled, permanently, and nothing but a pendant pairing of
> `CONFIG` will change that.

> **2026-08-20 — the program-path slice.** The tab gained a second thing to
> draw: a program's taught points and the path between them, resolved by
> `parsers/program_path.py` behind `get_program_path`. §2, §3, §4, §5, §6 and
> §8 carry it. Two structural facts worth reading before touching the file:
> the scene layer is now **five ordered `<g>` groups** rather than one
> `innerHTML` blob (same paint order, so translucent zones still wash over the
> arm), and the arm emitter moved into `armLayer()` so a per-frame redraw can
> rewrite that one group. **§8's headline changed**: the viewport is no longer
> untested — `tests/ui_view3d_probe.py` exists, and §9 item 2 is closed.

> **2026-08-10:** `view3d.js` `render()` now opens with a four-line branch:
> a backup whose `backup_type` contains `camera` hands the whole tab to
> `BV.cvx3d.render` (web/js/cvx3d.js — the Keyence model viewer, its own
> surface with its own doc trail). Everything below describes the robot
> path, which is unchanged.

## 1. What it is

The "3d view" tab — pinned to the `0` key (`keys.js:99-102`) — draws a
backup's DCS Cartesian Position Check zones **to scale** in a hand-rolled
SVG viewport, and poses the robot arm inside them at the backup's own
recorded joint angles. The side panel lists *every* DCS check (drawable or
not) with its pendant-style detail. The `dcs` tab is the same evidence in
report form: the signatures dashboard plus each verify-report section
rendered the way the pendant shows it — and it owns the status-pill and
detail primitives both screens share.

Underneath sit the subsystem's four pillars:

1. **The solve** — FANUC forward kinematics over a product-of-exponentials
   chain (`parsers/kinematics.py`), pendant-proven at 0.13 mm / 0.005°.
   The single most irreplaceable thing in this codebase: the convention was
   brute-forced against a controller's own position report and exists in no
   public document.
2. **The table** — 228 built-in robot-type chains
   (`kinematics_builtin.py`), five of them validated against 36 real
   robots, layered under user imports by `modeldb.py`.
3. **The projection** — a turntable camera + prism builder in ~120 lines of
   pure math (`proj3d.js`). Deliberately SVG, deliberately dependency-free:
   plant PCs on the WebView2 software-rendering rescue path must still get
   a working viewport, so WebGL is explicitly parked (ROADMAP, parking
   lot: "SVG stays the floor because rescue-mode PCs render in software").
4. **The zones** — `dcszones.py` turns DCSPOS.VA shadow tables + the
   verify report into drawable geometry with per-zone honesty flags.

Boundary with neighbours: `parsers/dcs.py` (the verify-report parser) and
the pendant-paired DCS value maps are parsing.md's territory — this doc
picks up where a parsed payload becomes geometry and pixels. `api.py`
contributes two endpoints (`get_dcs_zones`, `get_robot_pose`) that glue
session → parser → viewport.

## 2. The files

Per-file descriptions live in the [INVENTORY map](../INVENTORY.md). The
inventory's "3D viewer" group is 17 files; this doc claims the 11 that
ship (header). Within them:

| layer | files |
|---|---|
| pure parsers | `parsers/roboguidedef.py` (.def XML → chain), `parsers/kinematics.py` (the FK chain + `measure_flange`), `parsers/curpos.py` (CURPOS.DG pose + FRAME.DG tools), `parsers/dcszones.py` (zone payload), `parsers/program_path.py` (taught points → world mm) |
| data + registry | `kinematics_builtin.py` (the 228-type table — data, not parser, hence `src/backupviewer/` not `parsers/`), `modeldb.py` (built-ins under `%APPDATA%` imports, strict matching) |
| JS math | `components/fk.js` (the chain's JS twin), `components/proj3d.js` (turntable, frame transform, prism) |
| tabs | `tabs/view3d.js` (viewport + side panel), `tabs/dcs.js` (report pages; exports `BV.dcsDetail` / `BV.dcsStatusPill`) |

Load order matters (`index.html:118-136`): `proj3d.js` → `fk.js` → … →
`tabs/dcs.js` → `tabs/view3d.js`. view3d consumes dcs.js's exported pill
map and detail renderer — dcs.js must load first, and the comment at
`view3d.js:74-79` says why that dependency exists (§7, the fork).

Dual membership, per the inventory: `parsers/dcs.py` is primary *backup
parsing* (parsing.md's), with 3D viewer as its second home — the verify
report feeds both the dcs tab's pages and `dcszones`' merge. `tabs/dcs.js`
is primary 3D viewer with *compare engine* second (its `vs` toggle renders
two reports side by side through the trailing-`side` convention).

**Provenance of the model geometry.** The arm's schematic proportions and
the joint-frame conventions were cross-checked during development against
robot-model data reverse-engineered from Keyence `.rmd` files; the format
spec and its tooling live outside the repo in the git-excluded `robot
modelas/_re/` folder (six files in the inventory's 3D-viewer group), close
to real hardware references and deliberately unshipped. Nothing in the app
reads `.rmd` at runtime — the viewer draws from the `.def`-derived chains
above. If that data ever has to be regenerated, you need the excluded
folder (spec + reference parser + validators), the `.rmd` corpus it
documents, and a session with the format notes; nothing else in the repo
depends on it.

## 3. The flow

```
backup files                 parsers (pure)                 api                      frontend
DCSPOS.VA ──────┐
DCSVRFY.DG ─────┴─ dcszones.build_zones ─── get_dcs_zones ──────────── view3d draw()
                   (either may be absent,    cached "dcszones"           zones · TCP · warnings
                    not both)                (api.py:1384-96)
DCSVRFY.DG "Robot:" line ── modeldb.match ──┐
CURPOS.DG ── parse_curpos (group 1) ────────┤ get_robot_pose ────────── robot panel + BV.fk.chain
FRAME.DG ─── parse_tool_frames (active #) ──┘ uncached (api.py:1420-87)  posed skeleton + elements
                                              └ measure_flange gate:
                                                ok → flange_dz applied
                                                not ok → calib rides along, view refuses
```

A second, independent flow feeds the same viewport once a program is picked:

```
<PROG>.LS ─ ls_program.parse_ls_program ─┐
          └ ls_motion.parse_motions ─────┤
SYSFRAME.VA ─ frames.build_frames_model ─┼─ program_path.build_path ── get_program_path
POSREG.VA ── registers.parse_posreg ─────┤   (pure; kin optional)      cached per
CURPOS/DCSVRFY ── _robot_pose ───────────┘                             (file, chain identity)
                  └ _posable_chain: the SAME never-pose-on-contradiction
                    gate robotFrames applies, so a chain the backup's own
                    report contradicts places no joint-recorded point either
```

The chain identity rides the cache key (`progpath:FILE:TYPE:FLANGE_DZ`)
because `import_kinematics` changes what `modeldb.match` answers while a
session lives on — without it the old chain's placement would be served
forever.

The tab lights when the backup has `DCSPOS.VA` *or* `DCSVRFY.DG`
(`TAB_REQUIREMENTS`, `parsers/__init__.py:21-23`) — the arm is a bonus
layer on top, never a requirement. `get_robot_pose` returns every field
nullable ("All fields degrade to None - the view falls back honestly",
`api.py:1425`); §6 walks the ladder.

Inside `view3d.draw()` (`view3d.js:136-415`): world geometry per zone
(poly → prism → zone frame) + the posed skeleton + posed elements → world
bounding sphere → projector → **scene layer** in viewBox space (grid,
axes, arm, painter-sorted zone faces, wireframes) → **overlay layer** in
pixel space (labels, TCP crosshair, mm ruler, warnings, the snap cube).
State per tab visit lives in `BV.tabState("view3d")` — camera angles,
hidden zones, group filter, pan/zoom box, perspective flag, manual pose —
so the view restores exactly (`st()`, `view3d.js:25-44`).

What's pure vs stateful: everything in `kinematics.py` / `fk.js` /
`proj3d.js` / `dcszones.py` is pure (same input, same output — what makes
the twin probe possible). State lives in the session cache (`"dcszones"`),
`modeldb`'s `%APPDATA%\kinematics.json`, and the tab state above.

## 4. Domain truths

How each was verified, or an honest **assumed**. Tags per the template
note.

### The solve (the crown jewel)

| Fact | Evidence |
|---|---|
| The chain: `T_j = Trans(p_j)·Rz(r)·Ry(p)·Rx(w)` (home placement), `M_j = T_j·Rz(θ_j)·T_j⁻¹` (joint motion), link k = `M_1…M_k·T_k`, faceplate = `M_1…M_n·T_fp·Trans(0,0,flange_dz)`, world = CAD − ZeroOffset (translation only) | **pendant-paired**: brute-forced against the Roboguide testbed controller's own CURPOS.DG, winner at 0.13 mm / 0.005°, then validated on five robot families to <0.15 mm / ≤0.03° (`kinematics.py:3-14`, `roboguidedef.py:15-18`). **live-run 2026-08-01 (re-measured)**: the two pinned snapshots that can run the full pipeline both land inside those numbers — 0.011 mm / 0.0096° and 0.047 mm / 0.0043° (see the flange row) |
| A `.def` is **not** Denavit-Hartenberg: `OffsetCADToAxis` is the ABSOLUTE home placement of each joint frame in CAD coordinates; the joint rotates about that frame's local Z | `roboguidedef.py:4-10`; the simpler-than-DH reading is *why* the chain above works |
| θ_j = ±q_j (pendant degrees); `NegDirection="true"` flips the sign — J2 on every classic FANUC arm | `roboguidedef.py:10-12`; **live-run 2026-08-01 (re-measured)** census of the shipped table: 181/228 entries carry neg on J2; the exceptions are non-arm shapes (a gantry couples J1, the 7-axis type J7) |
| **`parallel: 2` on J3 is a mechanical fact, not a software quirk**: on a parallel-link arm the pendant's J3 value is slaved to J2, so the physical rotation is q3 **+** q2 — the forearm holds its attitude while the shoulder swings. To anyone who doesn't know FANUC arms this looks like a double-counting bug; deleting it mis-poses 181 of 228 types | `roboguidedef.py:11-13`, `kinematics.py:13-14`, `_thetas` (`kinematics.py:87-95`); test-enforced: `test_chain_j2_negdirection` asserts the forearm stays parallel to its home attitude through a J2 swing (`test_kinematics.py:106-116`) |
| The coupling is not only J3←J2: the 4-axis palletizer types (M-410iB/140H, R-1000iA/80H, the LR Mate HStud pair) chain **J3←J2 and J4←J3** — the linkage that keeps a palletizer wrist level — and the M-421iA couples J2←J1. Census: 181×(3←2), 4×(4←3), 1×(2←1) | **live-run 2026-08-01 (re-measured)** census of `BUILTIN`; the chain math handles all of them identically (`parallel` is per-joint, not J3-special) |
| `ZeroOffset` is the CAD→world shift; its Z is the J2 shoulder height (d1). Only the translation is applied — `to_world` subtracts `zero[0..2]` and ignores any rotational part | `roboguidedef.py:8-9`, `kinematics.py:11,114-120`; documented behaviour, but see §9 item 3 for the two table entries with a rotational ZeroOffset |
| Chains are not all 6-axis: the table holds 2-, 3-, 4-, 5-, 6- and 7-joint machines (histogram 1/7/39/13/167/1) — delta pickers (DR-3iB, 4 joints, three 120°-splayed axes), a gantry, a 7-axis arm. `chain_frames` takes the chain as given | **live-run 2026-08-01 (re-measured)** census; wellformedness test runs every shipped chain (`test_modeldb.py:66-86`) |
| Faceplate home orientation on a classic arm: local Z along world +X (the mounting face looks forward) | test-enforced, `test_kinematics.py:96-97` |

### The -IF flange plates (self-measured)

| Fact | Evidence |
|---|---|
| "-IF" dress variants are the same arm plus an adapter plate the plain library `.def` omits: +23.0 mm on R-2000iC/210F-IF and R-1000iA/100F-IF, +10.06 mm on M-900iB/280L-IF, 0 on plain robots | **live-run 2026-07-18 (recorded)** across the fleet, `kinematics.py:15-19` |
| Those constants are *records*, not applied values. Every backup **measures its own plate**: `measure_flange` poses the chain at the backup's CURPOS joints, composes the taught tool from FRAME.DG, and compares against the controller's own world TCP; the residual is expressed **in the flange frame** (which is exactly why a plate reads as pure Z), and the correction is trusted only when it *is* pure flange-Z — `dxy < 1.5 mm` and orientation `< 0.1°` | `kinematics.py:125-148`, applied at `api.py:1464-71`; test-enforced incl. the refusal: a chain bent by 50 mm does **not** calibrate (`test_kinematics.py:125-140`) |
| The per-robot measurement is stable: the same pinned robot, two pulls a month apart, measures +9.955 mm and +10.071 mm — bracketing the recorded +10.06 constant for its family, with residuals 0.011 mm / 0.0096° and 0.047 mm / 0.0043° | **live-run 2026-08-01 (re-measured)** on the primary and prev fixture pins, through the shipped builtin table only |
| Anything that is *not* a pure plate refuses honestly: rail-mounted 210L robots fail by hundreds-to-thousands of mm of pure translation (the carriage axis is unmodeled; a J7 value in CURPOS is ignored), and one odd-mount family fails by ~160° of orientation. The gate catches each individually and the arm is not drawn | **live-run 2026-07-18 (recorded)** fleet sweep; in-repo trace is CHANGELOG's "kinematics we don't parse yet" note. Not re-provable from the pins (none is a rail); see §9 item 4 |

### Type identity and matching

| Fact | Evidence |
|---|---|
| The robot type name is **not in the `.def` XML** — the filename carries it. The in-file envelope (`RANGE_*.rcf`) names are shared reach shells (one file covers 165F/210F/240F) and sometimes plain wrong (the CRX-30iA def points at the 25iA shell) — naming from them mislabels imports | `roboguidedef.py:20-24`, `modeldb.py:88-91`; the wrong-shell case was **found by probe** during the build (live-run 2026-07-18, recorded) |
| The backup's own type string comes from the DCS verify report's robot-setup `Robot:` line — no DCSVRFY.DG (or a report without that line), no arm | `api.py:1428-38`; **live-run 2026-08-01 (re-measured)**: two of the four pins carry a DCSVRFY.DG whose report has no robot-setup line, and both stay honestly unmatched |
| Matching is exact on `normalize_type` keys (uppercase alphanumerics), else exact after dropping a whitelisted trailing dress token — the whitelist is `{"IF"}` alone, fleet-validated as arm-identical. **Never a loose prefix**: 210F would silently borrow for a missing 210FS, the plain 120iD wrist for a /35 | `modeldb.py:14-21,113-132`; test-enforced: `10S` and `/35`-style near-misses return None (`test_modeldb.py:31-37`) |
| The shipped table: 228 types; **5 validated** (ARC Mate 120iD ×1 robot, CRX-30iA ×2, M-900iB/280L ×12, R-1000iA/100F ×4, R-2000iC/210F ×17 — 36 robots, all dated 2026-07-18). A `validated` block records `{robots, max_xy_mm, max_ori_deg, date}` | `kinematics_builtin.py` + docstring; **live-run 2026-08-01 (re-measured)** census; wellformedness test asserts ≥200 entries, ≥5 validated, sane bounds (`test_modeldb.py:71-86`) |
| What the viewer says per tier: validated → "built-in, validated on N robots (≤X mm)"; unvalidated + position report → runtime self-verify (the measure_flange gate) still guards the pose; unvalidated + **no** position report → the arm poses "on the table's word alone", pill "unverified", note "robot pose unverified — no position report in this backup". That three-tier honesty is this subsystem's version of the DCS `$MODE` contract | `kinematics_builtin.py:8-15`, `view3d.js:363-369,637-649,682-689` |
| User imports layer **over** built-ins (imports win), each entry tagged `source_kind`; importing needs nothing but a Roboguide install's Robot Library folder, auto-detected at the standard ProgramData path when present | `modeldb.py:58-64,139-151`; test-enforced (`test_modeldb.py:48-63,89-96`) |

### CURPOS pairing and posing

| Fact | Evidence |
|---|---|
| CURPOS.DG's active tool number sits on the line directly above each world block — positional pairing, no labeled association | `curpos.py:64-67`; parsing.md §4 owns this row — linked, not restated |
| Only group 1 poses the arm: `get_robot_pose` reads `groups[0]` for joints/tool/world. Further motion groups (servo guns, positioners) contribute zone *filters* but never arm geometry | `api.py:1447-54`; **live-run 2026-08-01 (re-measured)**: two pins report 2 CURPOS groups; group 1 carries the 6 robot joints |
| The pose is editable per joint in the panel (pill flips to "manual"); reset returns to the backup's own snapshot. A backup with no CURPOS poses at home (all zeros) | `view3d.js:96-103,706-743` |

### The projection

| Fact | Evidence |
|---|---|
| The turntable camera is closed-form and defined for EVERY az/el — poles included and past them: screen-up is the elevation tangent (unit, perpendicular to the eye), so there is no gimbal snap and no degenerate "right" vector anywhere | `proj3d.js:20-33` |
| …and the tab deliberately does not use that freedom: elevation clamps to exactly ±90 (§7, the flip). The projector keeps the capability; the UI keeps the sanity | `view3d.js:41-43,549-554` |
| Orthographic by default — distances stay measurable (the mm ruler and the to-scale promise depend on it). Perspective is an optional wrap that foreshortens about the scene center: points at the center's depth are unchanged, so fit/pan/pivot math done there is exact in both modes, and the divisor is clamped so geometry never crosses the eye | `proj3d.js:5-9,38-47`; the toolbar spells the trade out ("off = orthographic (parallel, true to scale)", `view3d.js:907-910`) |
| Painter's order: `project()` returns depth ascending toward the viewer; faces of **all** zones are sorted together, not per zone, so overlapping zones stack right | `proj3d.js:5-8`, `view3d.js:280-293` |
| Auto-fit is rotation-invariant: it fits the world bounding **sphere**, which projects to the same circle at every angle — so orbiting cannot make the view "breathe". Bonus: mm-per-px matches across all views. Perspective's near-side magnification (≤ D/(D−R) = 1.4) is covered by the pad | `view3d.js:177-204` |
| Zone colors rotate the theme accent's hue by the golden angle (137.508° per zone number): every theme keeps its character and 32 zones stay tellable. No hardcoded colors anywhere in the tab | `view3d.js:47-71`; the CLAUDE.md theme rule, instantiated |
| The FANUC xyzwpr rotation (`Rz(r)·Ry(p)·Rx(w)`) is hand-implemented **three times**: `kinematics.frame`, fk.js `frame`, proj3d `frameTransform`. The first two are held equal by the probe; proj3d's copy is enforced by nothing (§8) | `kinematics.py:68-72`, `fk.js:57-62`, `proj3d.js:70-92` |

### Zones and models on screen

| Fact | Evidence |
|---|---|
| A drawable zone is an XY polygon **in its DCS user frame**, extruded z1..z2 into a prism, then carried to world by `frameTransform` — a zone's numbers only make sense through its frame, so the frame rides the payload per zone | `dcszones.py:1-10`, `view3d.js:143-149`; geometry/heuristics test-enforced (`test_dcszones.py`) |
| Honesty flags survive to pixels: a zone whose frame is missing (or one rebuilt from the verify report's Point 1/Point 2 with no DCSPOS.VA) draws frame-less, wears an "approx" pill, and the viewport prints "⚠ frame rotation unknown — geometry approximate" | `dcszones.py:381-418`, `view3d.js:360-362,765`; the fallback path is test-enforced (`test_dg_fallback_is_flagged_approx`) |
| Keep-out zones fill heavier than keep-in (0.26 vs 0.10 opacity) and keep-in wireframes dash — you read containment at a glance without color semantics | `view3d.js:283-284,295-303` |
| The TCP crosshair is the position captured **when the verify report was written** (the first enabled zone's "Current" column), transformed through that zone's frame — labeled "tcp", never confused with the live arm | `dcszones.py:568-588`, `view3d.js:150,341-347` |
| Joint position / speed checks are listed data-only: "nothing honest to draw without a robot model" — a joint limit is a body volume, not a box | `view3d.js:8-11,779-801` |
| DCS user-model elements draw only while the arm is posed, and only when: enabled, structured (a `shape_raw` from the .VA), **not** tool-frame-attached, and on the faceplate (link 99) or a resolvable numbered link. Link-attached ones draw **dashed** — the link-frame convention is unverified — with the viewport warning "link-attached elements: link-frame convention unverified"; faceplate ones draw solid (the faceplate frame is the pendant-proven one) | `view3d.js:115-134,263-277,370-372` |
| Elements carrying a `utool_num` are excluded from drawing entirely — their positions are relative to a taught tool frame the draw path does not compose (**assumed** semantics; the exclusion is the honest choice under either reading) | `view3d.js:121`, field meaning per `dcszones.py:439` ("Tool frame") |
| Spheres project as circles, capsules as round-cap strokes, **in world mm** — model geometry scales with the scene like everything else. The arm body itself is deliberately schematic: capsule limbs sized from reach (girth = clamp(reach·0.045, 30, 110), tapering pedestal→wrist), "just enough girth to read as a robot", never claiming to be the DCS robot model or a mesh | `view3d.js:237-262`; the labeling ruling is ROADMAP's ("hand, not the DCS model") |

### The program path

| Fact | Evidence |
|---|---|
| A taught cartesian point is a pose **in its user frame, measured to the tool**: `T_tcp = frame(uframe)·frame(point)`, and the faceplate the solver would target is `T_tcp·inv_rigid(frame(utool))` — the **inverse of the composition `measure_flange` already proves** against a controller's own report (`kinematics.py:137`) | reused, not re-derived; test-enforced by a round-trip that recovers the original faceplate to 1e-9 (`test_program_path.py`) |
| A joint-recorded point needs no frame composition — forward kinematics places it exactly, through the same pendant-proven chain the arm poses on. That is the honest tier: pill `exact`, no solver anywhere in it | test-enforced (`test_joint_point_places_exactly_through_forward_kinematics`), probe-enforced (`prog.joint_point_exact`) |
| `UF: 0` / `UT: 0` are the world frame and the faceplate. They resolve to identity **with no `SYSFRAME.VA` at all** and are not flagged missing — same reading `dcszones.py:350-351` already takes for `ufrm_num == 0` | test-enforced (`test_uframe_zero_is_world_and_needs_no_frames_file`) |
| `uf`/`ut` stay **strings** end to end. `"F"` (use whatever is current) is legal, and `parseInt("F")` is `NaN` → frame 0 → a point that looks right and is wrong. `"F"` resolves through the backup's own active number and is reported as an **assumption**, because "current" is a run-time fact | `ls_edit.py:330` validates `\d+|F`; test-enforced |
| The destination of a move is the reference **immediately after the motion letter**, never the first `P[..]` in the line — options carry their own (`Offset,PR[7]`, `Skip,LBL[3]`) | parsing.md §2 owns the grammar; 13 parametrised cases in `test_ls_motions.py` |
| Durations are **derived** (a linear feedrate over a computed distance, or a time-specified move), **assumed** (a percentage move, priced at `ls_motion.ASSUMED_JOINT_DEG_S`, because no backup file records per-model maximum joint rates), or **unknown** (a register-driven speed) — and each step says which | test-enforced (`test_ls_motions.py` duration group) |
| Every move that cannot be placed is **listed with its own reason**, never dropped: no `/POS` entry · anonymous `P[...]` · indirect `P[R[n]]` · masked · uninitialized `PR` · a `PR` a running program writes · a missing or uninitialized frame · `UF: F` with nothing active · the wrong motion group | test-enforced per reason; probe-enforced that the row and its note reach the DOM (`prog.refused_listed`) |
| A position taught only for motion group 2 never supplies numbers for the group-1 arm | test-enforced (`test_group_two_numbers_are_never_used_for_the_group_one_arm`) |
| `CONFIG` is carried **verbatim and never decoded**. Its letters (flip/up/front) and turn numbers have not been pendant-paired in this repo, so no meaning is claimed for them | the raw string rides every step's detail block |

### The inverse solve

| Fact | Evidence |
|---|---|
| Damped least squares, `Δq = Jᵀ(JJᵀ + λ²I)⁻¹b`, **always a 6×6 solve** whatever the joint count — so the 4-joint delta, the 6-axis arm and the 7-axis type share one code path. Too few joints leaves residual in the DOFs the chain lacks (the gate then refuses); too many gives the minimum-norm step, which stays near the warm start, so branch continuity is free | `kinematics.solve_ik`; the short-chain refusal is test-enforced |
| The Jacobian is **analytic**, from the chain's own algebra: `frames[i] = acc_{i-1}·T_i·Rz(θ_i)`, and `Rz` about the local z changes neither the translation nor the third column, so `ω_i` is that third column, `p_i` its translation, `v_i = ω_i × (p_tcp − p_i)`. One forward pass per iteration instead of the seven a finite-difference Jacobian costs | test-enforced against a finite-difference Jacobian over **every distinct chain shape** (12 today, deduped on joint count + the neg/parallel tuples so the sweep grows with the table); worst relative difference **8.7e-09** |
| The neg/parallel coupling in the Jacobian is `_thetas`' rule, read off the same chain dict and never restated: `∂θ_i/∂q_k = s_i` for `i == k`, plus `s_i` again when joint `i` is a parallel link mastered by `k` | test-enforced per chain shape (`test_coupling_is_the_same_rule_thetas_uses`) — a drift here mis-poses the forearm on 181 of the 228 types |
| Orientation error uses the `atan2` form with a near-π fallback, not the skew part alone: a cold seed's first iteration routinely sits past 90°, where the skew part shrinks back toward zero and would send the step the wrong way | test-enforced at 0.5°, 30°, −95°, 120°, 179.5° and exactly 180° |
| `ok` is an **honesty gate, not convergence**: the answer is accepted only when running it back through the forward chain reproduces the target inside 0.5 mm / 0.05°. On failure the **best iterate seen** is returned, so the residual reported is the residual of the joints handed back | test-enforced both ways |
| Seeds are tried in order — the previous step's answer (CURPOS for the first), then home, then four spread poses — and the ladder **stops early when two seeds land within 1% of each other**: the extra seeds exist to find a different *branch*, and a branch change cannot make an out-of-reach point reachable | measured: a 600-move program with 109 genuinely out-of-reach points went 75 s → 11 s; an all-reachable 200-move program solves in ~1.0 s |
| A joint-recorded point is posed by its **own taught angles** — no solver, no branch to choose, exact — and is labelled `exact` to distinguish it from `solved` | test- and probe-enforced |
| **The branch is the one thing no runtime check can catch.** A solution on a mirrored elbow reaches the same TCP with a ~0 residual. Warm-starting from the previous step keeps the branch *continuous*, which is what a real motion does; it does not make it *the taught one*. Every solved posture is labelled and the viewport says so | `test_warm_start_recovers_the_branch_not_just_the_pose` pins recovery to 0.5° from a warm seed; beyond that it is labelled, not proven — see §9 |

### Playback

| Fact | Evidence |
|---|---|
| One interpolation rule: **lerp in joint space between consecutive knots**. A joint move satisfies it *exactly* — a FANUC joint move IS a joint-space lerp, all axes starting and stopping together — and a linear or circular move satisfies it to the density of the knots the solver walked along the drawn line. There is no per-motion-type branch in the render loop | `timeline`/`qAt` in `view3d.js`; the knots come from `program_path.build_pose` |
| Substeps are `ceil(dist/25 mm) + ceil(ori/5°)`, capped at 40 per move and 4000 per program, and the cap is **reported** (`budget.scaled`) rather than silently applied | `program_path` |
| A move's duration is **derived** where the listing proves it (a linear feedrate over the computed distance, or a time-specified move) and **assumed** where it does not (a percentage move, priced at `ASSUMED_JOINT_DEG_S` over the joint travel the solve revealed). A register-driven speed stays **unknown** and the viewport says the run is a path preview, not a cycle time | `ls_motion.step_duration_ms` + `program_path._price`; `timing` counts ride the payload |
| No acceleration, no deceleration, no CNT blending. Said on screen, every time, next to the arm | the viewport note |
| Selecting a move and the playhead are **one state**: picking a step seeks to that move's arrival, so a full redraw cannot leave the highlight and the clock disagreeing | `pick`/`endOfStep`; probe-enforced |
| A playhead exactly on a boundary belongs to the segment that **ends** there — "arrived at this move", not "starting the next". The two segments agree on the joints at that instant, so nothing jumps | `segAt`; this was a real bug, found by the probe |

## 5. Invariants

What must stay true, what enforces it, what breaks if it doesn't:

1. **`kinematics.py` is the reference; `fk.js` is the twin; the probe is
   the contract.** The math is duplicated deliberately — Python cannot run
   in the viewport (a bridge call per orbit tick), JS cannot be the
   reference (the pendant pairing happened against the Python chain).
   `tests/ui_fk_probe.py` (the tenth probe, `c502ad5`) feeds identical
   chains and joint sets to both and asserts every matrix cell of every
   joint frame + faceplate within 1e-6, across 11 chains × 3 poses (every
   validated type, the longest chain, an even spread, the synthetic
   parallel-link arm — NegDirection/ParallelLink/flange-dz always
   exercised). Measured agreement ~1e-13 — five orders inside tolerance.
   Before `c502ad5` the docstrings *claimed* a check that did not exist,
   and a drift would have mis-posed the arm with every test green. Change
   the chain in one language and not the other and the probe fails; that
   is the design.
2. **Never pose on contradiction.** When a backup carries enough to check
   (CURPOS + active tool + world TCP) and the check fails, the arm is not
   drawn — `robotFrames` returns null on `calib && !calib.ok`
   (`view3d.js:107-110`) and the note prints the residual. `flange_dz` is
   applied only when `calib.ok` (`api.py:1470-71`). A wrong pose inside a
   safety-zone display is the worst lie this screen could tell.
3. **Never borrow an arm.** `modeldb.match` is exact-or-whitelisted-dress,
   never a loose prefix (`modeldb.py:118-132`); unknown variants stay
   honestly unmatched and the panel leads with the import path. Test-
   enforced (`test_modeldb.py:31-37`).
4. **Imports win over built-ins, visibly.** `merged()` layers
   `%APPDATA%\kinematics.json` over `BUILTIN`, tagging every entry
   `source_kind` so the panel can say which table answered
   (`modeldb.py:58-64`; test-enforced).
5. **The shipped table stays runnable.** The wellformedness sweep runs
   every one of the 228 chains and checks keys normalize, joint numbers
   are 1..n, and validated blocks carry sane provenance
   (`test_modeldb.py:66-86`). A table entry that parses but cannot pose
   would otherwise fail only when a tech opens that robot.
6. **Zone numbers pass through their frame, or say they didn't.** Frames
   resolve by (group, user-frame *number*) — not slot
   (`dcszones.py:130-147`); a zone whose frame is absent is flagged
   `frame_missing`, the DG-rebuilt fallback is always `approx`, and the
   flags render as pills + the viewport warning. Enforced by
   `test_dcszones.py`.
7. **One status map, one detail renderer.** `BV.dcsStatusPill` and
   `BV.dcsDetail` are owned by `tabs/dcs.js` (loaded before view3d,
   `index.html:128,136`) and consumed by the 3D panel. The private copy
   this replaced disagreed (§7). A new consumer uses these, never a fork.
8. **Layers degrade independently; evidence never vanishes.** The tab
   needs DCSPOS.VA *or* DCSVRFY.DG; the arm additionally needs a type
   string + a match; verification additionally needs CURPOS + FRAME.DG +
   world. Each missing piece removes exactly its own layer with a stated
   reason (§6) — and disabled/unconfigured checks stay listed behind
   "show disabled", never dropped (`view3d.js:88-94,614-621,917-927`).
9. **Geometry in viewBox space, text in pixel space.** The scene layer
   holds only geometry; every label, warning, ruler and the snap cube live
   in the pixel overlay re-projected per draw — zoom and orbit move the
   world, never the text size (`view3d.js:209-212,310-320`). The gesture
   math and the overlay share the one uniform meet-scale (§7).
10. **One contradiction gate, two consumers.** `robotFrames`
    (`view3d.js`) and `api._posable_chain` both refuse the chain when
    `calib && !calib.ok`. The program path passes through the second, so a
    backup whose kinematics contradict its own position report gets **no
    arm and no FK-placed point** — it still gets its cartesian path, because
    that composition never touched the chain. Probe-enforced
    (`gate.no_arm`, `gate.joint_point_refused`, `gate.path_still_drawn`).
11. **The scene is five ordered groups, and the order is the paint order.**
    `v3-l-base` → `v3-l-path` → `v3-l-arm` → `v3-l-zone` → `v3-l-wire`.
    Zones are translucent and deliberately wash over the arm; reordering
    these silently changes what the screen says about containment.
    Probe-enforced (`view3d.layer_order`).
12. **The fit is sized for the whole run, once.** A program's placed points
    join the world bounds at load, so the bounding sphere already covers
    every point the arm will visit. Growing the bounds per frame is the same
    trap the projected-bounding-box fit was (§7): the view pumps.
13. **A frame rewrites two nodes and nothing else.** `drawArm` touches the
    `v3-l-arm` group and the `v3-ovl-live` overlay group. The projector, the
    viewBox, the fit, the painter's sort, the zone labels, the ruler, the
    notes and the cube all stay as the last full draw left them — and a full
    draw re-applies the playhead afterwards, so the two can never disagree.
14. **Nothing poses on a chain the backup contradicts, whatever the route
    in.** `poseGate(robot)` in `view3d.js` is the single JS predicate and
    `robotFrames` is its only caller; `api._posable_chain` is the same rule on
    the Python side. Adding a new path to `BV.fk.chain` that skips the gate
    would draw arms the still frame refuses.
14. **Everything the viewport claims is in mm, to scale.** The grid step,
    the axes (one grid-step long), the ruler, model radii, zone extents —
    all world mm through the same projector. No screen-space fudge factors
    on geometry; that is what "drawn to scale" means here
    (`view3d.js:213-236,349-357`).

## 6. Failure modes

The degradation ladder: what evidence is missing or contradicts → what the
screen says afterwards. "Verified" = a test/probe pins it; "measured
2026-08-01" = exercised on the pinned fixtures today; "traced" = read off
the code this pass, held by nothing.

1. **No DCSPOS.VA and no DCSVRFY.DG** → the tab does not exist for this
   backup (`TAB_REQUIREMENTS`); reaching the endpoint anyway raises
   `MISSING_FILE` and the tab shell shows "no DCS zone data" + the reason
   (`api.py:1392-93`, `view3d.js:944-947`). Verified at the manifest level
   (`test_backup_formats` per-format tabs); the error branch traced.
2. **DCSVRFY.DG only (no DCSPOS.VA)** → zones rebuild from the report's
   Point 1/Point 2 tables: diagonal boxes only, frame-less, every zone
   `approx`, source `"dg"`. Verified (`test_dg_fallback_is_flagged_approx`).
   Measured 2026-08-01: two pins take this path — and their reports carry
   no pos-tables at all, so they draw zero zones and the viewport says "no
   cartesian zones in this backup" while the panel still lists whatever
   checks exist. Absence displayed, not invented.
3. **DCSPOS.VA only (no verify report)** → full geometry, no pendant
   statuses, detail blocks synthesized in the pendant's shape
   (`_synth_detail`), no TCP crosshair (it only exists in the report).
   Verified (`test_empty_sources_stay_total`, `test_va_geometry_…`).
4. **A zone references a user frame the dump doesn't hold** →
   `frame_missing`: drawn frame-less at its raw numbers, "approx" pill,
   viewport warning. Verified (`test_dcszones.py`).
5. **No robot type string** (no DCSVRFY.DG, or a report without the
   robot-setup `Robot:` line) → no arm; the robot row shows "unknown type"
   + "no kinematics for this type". Measured 2026-08-01: two pins are
   exactly this case. UI wording traced.
6. **Type known but not in any table** (the /35-style gap) → no arm; the
   row auto-opens leading with the fix: plain-language hint, one-click
   "import from this PC's Roboguide" when an install is detected,
   "pick folder…" fallback (`view3d.js:650-681`, `modeldb.default_library`).
   The registry side is verified (`test_modeldb.py:89-96`); the UI flow was
   probe-verified on build day but that probe never landed (§8) — traced
   today.
7. **Matched, but no CURPOS** → the arm poses at home (all zeros), pill
   "home", note "robot pose unverified — no position report in this
   backup". Traced (`view3d.js:98-103,367-369`).
8. **Matched + CURPOS, but no FRAME.DG row / no world TCP** → poses at the
   backup's joints, `calib` stays null, same unverified note. Traced
   (`api.py:1456-67`).
9. **The check itself fails** (rail carriage, odd mount, bent arm, wrong
   table entry) → the arm is **not drawn**; the panel pills "mismatch" and
   the viewport prints the residual ("kinematics mismatch vs backup
   position report (N mm / N° residual) — robot not posed"). The gate is
   test-enforced with a bent-arm case (`test_kinematics.py:136-140`); the
   refusal UI is traced (`view3d.js:107-110,363-366`); the real-world
   catches (rails, mounts) are live-run 2026-07-18 (recorded).
10. **Disabled or uninitialized safety entries** → never read as enabled;
    hidden by default, listed behind "show disabled", dimmed with their
    pills. The parser side is the `coerce_scalar` repair (parsing.md §6's
    trap, `c0c0968`) and stays test-enforced (`test_dcszones.py`
    disabled/uninit honesty trio).
12. **A program whose moves cannot be placed** → each move is listed in the
    program section, dimmed, with the sentence that says why; the viewport
    prints "⚠ N of M steps not placed — see “program” on the right". The
    placed ones still draw. Verified (`ui_view3d_probe`, `prog.refused_*`).
13. **A program picked on a backup with no matched chain** → cartesian
    points and the path still draw (their composition never needed the
    chain); joint-recorded points refuse with "a joint-recorded point needs
    the robot's kinematics to place". Verified (`test_program_path`), and
    the contradiction case is probe-verified end to end.
15. **A taught point the arm cannot reach** → the point still draws (the
    backup does say where it is) and its step row goes amber with `not
    reached`, carrying the residual the solver actually achieved. Distinct
    from `not placed`, which is the backup not saying where the point is —
    two findings, two classes, never collapsed. Verified (`ui_view3d_probe`).
16. **Probe environment** (and any headless WebView2): no
    `requestAnimationFrame`, synthetic pointers with no capturable id —
    `setPointerCapture` is wrapped in try/catch so a probe drag cannot
    throw. Everything except playback still renders statically per draw
    call, which is why the tab works at all in a hidden window; playback
    itself schedules through `requestAnimationFrame` **or** `setTimeout` and
    advances the playhead off `Date.now()` in both paths — never off a frame
    count, so a throttled hidden window advances by real time instead of
    stalling, and `dt` is clamped to 1 s so one throttled tick cannot
    silently finish the run. The loop self-terminates on
    `!document.contains(svg)`, the same way `cvx3d.js` and `overview.js` do,
    because the router empties the slot rather than calling a teardown.
    Probe-verified with rAF removed **before** the tab renders — `HAS_RAF` is
    read once, so deleting it afterwards would prove nothing.

## 7. Traps paid for

- **Two ways to say "no", and they mean opposite things.** "Not placed" is
  the *backup* failing to record where a point is; "not reached" is the
  backup recording it fine and the *arm* being unable to get there. They were
  briefly one dimmed row, which reads as "this program is half broken" when
  the truth might be "this robot is on a rail we do not model". Separate
  classes, separate pills, separate counts.
- **The forked status map.** The 3D panel once carried its own
  status→pill copy; it disagreed with the dcs tab (the same zone read
  green on one screen and red on the other) and tested for a `"SAFE"`
  status no parser has ever emitted. Fix: one map + one detail renderer
  exported by dcs.js (`1e93b75`; `dcs.js:31-46`, `view3d.js:74-79`). The
  composition rule ("extend the primitive, never copy-paste a variant")
  applied to a lookup table.
- **The flip at the pole.** Elevation was briefly unbounded (the projector
  genuinely supports it, `proj3d.js:20-23`) — crossing 90° inverted the
  world's screen-vertical *seamlessly*, and exactly at the pole the snap
  cube is face-on from both sides, so nothing warned you which way you
  were looking. Over-the-top orbiting read as a portal, not a feature. Fix:
  clamp elevation to **exactly** ±90 — the older complaints came from a
  ±89.5 clamp (top view never quite top), so the poles must stay exact
  (`view3d.js:41-43,549-554`).
- **Auto-fit that breathed.** Fitting the projected bounding box made the
  zoom pump while orbiting (the box's extent changes with angle, even
  spinning in place). Fix: fit the world bounding sphere — same circle at
  every angle (`view3d.js:196-204`).
- **Letterboxed pan lag.** `preserveAspectRatio="meet"` scales the viewBox
  uniformly and letterboxes the slack axis; converting px→viewBox with
  width/height *independently* made pan track a fraction of the mouse on
  whichever axis was letterboxed. One uniform scale for both axes,
  everywhere (`view3d.js:474-488`).
- **The default view sat on the 45° grid.** Plant fences love 0/45/90°
  orientations; a wall parallel to the eye azimuth degenerates to a
  sliver — seen on a real −45° fence. The default is deliberately odd
  (az −24.8, el 36.8) and the comment says "do not tidy these"
  (`view3d.js:29-34`).
- **Envelope names are shared shells — and sometimes wrong.** Naming
  imports from the `.def`'s RANGE reference mislabeled robots (one shell
  file serves 165F/210F/240F; the CRX-30iA def points at the 25iA shell,
  found by probe on build day). The filename is the identity
  (`modeldb.py:88-91`, `roboguidedef.py:20-24`).
- **A disabled safety zone read back as ENABLED** — the `dcszones` private
  `_value` fork; owned by parsing.md §6, fixed at `c0c0968`. Repeated here
  in one line because this subsystem is where the wrong answer would have
  been *drawn*.
- **Generating the table with `json.dumps` bricked the module.** The
  228-entry `kinematics_builtin.py` is generated Python; emitting it via
  `json.dumps` wrote `false`/`null` → `NameError` at import — and the
  broken module then blocked its own regenerator from importing. Use
  `repr`/`pformat`, and stub the module before regenerating. Recorded from
  the build session (2026-07-18); the generator script itself is not in
  the repo (§9 item 6).

## 8. Coverage

Counted 2026-08-01, re-counted 2026-08-20. Full-suite anchor: `python -m
pytest tests -m "probe or not probe"` → **865 passed, 2 skipped** on
2026-08-20 (the two skips are the private-fixture gate reporting itself
absent on a clean clone, which is the behaviour it exists to have). The
2026-08-01 anchor was 701 passed / 0 skipped.

**Tracked, direct — 27 unit tests + the probe across 4 files.**
`test_kinematics` (11: `.def` parse incl. the dress/envelope exclusions,
non-robot rejection, normalize/filename rules, FK home/J1/NegDirection+
parallel/flange-dz poses against hand-derived expectations,
`measure_flange` recovery + bent-arm refusal, CURPOS/FRAME parsing, the
full synthetic pipeline); `test_modeldb` (4: import/match/layering, the
228-entry wellformedness sweep, default-library detection);
`test_dcszones` (12: VA geometry/frames/heuristics, user models, both
method vocabularies, target-name normalization, verify-merge precedence +
TCP, newer-pendant vocabulary, DG-fallback approx flag, target-note
annotation, empty-source totality, and the disabled/uninit honesty trio);
`ui_fk_probe` (the tenth probe: 35 checks — boot, case-count, and 33
twin-equality cases at 1e-6 tolerance, ~1e-13 measured).

**Excluded/private, adjacent.** `test_dcs` (12, on the primary pin) pins
the verify-report sections both tabs render — parsing.md's suite serving
this subsystem. The four pins also carry CURPOS/FRAME, which is what made
today's re-measurements possible (§4).

**Re-measured this pass** (scratch script over the pins, shipped table
only, printed as numbers): the full pose pipeline on two snapshots
(residuals + measured plates), the honest-unmatch path on two more, the
zone payload census (32 CPC slots / 2 enabled / 16 models / TCP present on
the rich pin; zero drawable zones honestly reported on the DG-only pins).

**The viewport is under test now — 2026-08-20.** `tests/ui_view3d_probe.py`
(registered in `test_probes.py`) boots the tab in a hidden WebView2 on three
fabricated backups and asserts on real DOM: **57 checks**. The baseline this
doc used to call untested — zones drawn, arm posed with real geometry, the
five-group layer order, the cube snapping *and* refitting, elevation
clamping to exactly 90 however it got out of range, and per-tab state
surviving a round trip — plus the program path (steps listed, path drawn,
markers matching the placed count as an *invariant* rather than a literal,
the refused move listed with its note, the viewport note, step selection in
list and viewport, the picker filtering, the toggles) and both honest-refusal cases end
to end: the contradiction gate (no arm, no FK-placed point, cartesian path
still drawn, the residual note printed) and the **untyped backup** - no
robot-setup line, so no type and no chain, which is what two of the four
pinned sample backups actually are. There the zones and the cartesian path
still draw, the joint-recorded point says it needs kinematics, there is no
player, and nothing claims a posture it did not solve. No private tree: fabricated `DCSPOS.VA`,
`DCSVRFY.DG` naming a shipped builtin type, `CURPOS.DG`, a `.LS` with both
point representations, `RB…` names under `FakePlant`.

What that does **not** yet pin: the EOAT capsule rendering, the first-run
kinematics-import flow, and the `.def` import dialog — still traced only.
The untracked full-app probe (`ui_probe.py`, hand-run) remains free of any
`.v3-*` assertion; the tracked probe is the one that counts now.

**Also uncovered:** `proj3d.js` — zero direct tests anywhere (turntable
basis, perspective wrap, unproject, prism topology; its `frameTransform`
is the one unpinned copy of the WPR rotation, §4); `tabs/dcs.js` renderers
beyond the hand-run probe; `import_kinematics`' dialog path; multi-group
CURPOS beyond group 1; `curpos.parse_tool_frames` section-end edges
(parsing.md §7 already flags it).

## 9. Open questions

Found during this pass; recorded, not fixed (ground rule: no feature code
changes). Evidence attached.

1. **CORRECTED THIS PASS — two stale "data-only" comments.** Both said
   user-model elements cannot be placed for want of kinematics — true when
   written, false since the posed arm landed (`94b81cc`, 2026-07-18):
   `view3d.js`'s user-models panel comment claimed "placing one needs
   kinematics we don't have" while `posedElements` in the *same file*
   places them on the FK frames, and `dcszones.py`'s docstring tail said
   "placing them needs kinematics" as if that were the end of it. Both
   replaced with the truth, 1-for-1 line swaps (the only code-file touches
   this pass makes). ROADMAP's ✅ user-models line still carries the same
   vintage phrasing ("data-only in the panel until kinematics can place
   link-attached shapes") — accurate as a historical record, read it with
   the date in mind.
2. **CLOSED 2026-08-20 — the viewport probe gap** (§8). `tests/
   ui_view3d_probe.py` does exactly what this item asked for: a tracked
   hidden-window probe on a fabricated backup, zones drawn, arm posed, the
   el clamp, state restore, plus the program path and the contradiction
   gate. §6's rows 5–9 are still partly traced (no-type and no-CURPOS
   ladders are not fixtured yet) — the *gap* is closed, the *ladder* is
   next.
   **New, in its place:** the taught `CONFIG` string is carried but never
   decoded, so nothing yet checks a placed point's branch against what the
   robot actually did. That only starts to matter when the inverse solve
   lands (ROADMAP: the OPW + CONFIG lane); `CURPOS.DG` prints joints AND the
   config string the controller computed for them, which is the free
   ground-truth pair when someone takes it — `parse_curpos` reads the six
   world floats today and drops that string.
3. **Two F100iA entries carry a rotational ZeroOffset the world shift
   ignores.** `chain_frames` subtracts only `zero[0..2]` (documented,
   `kinematics.py:11`), but `F100IA104`/`F100IA104L` ship
   `zero = [-82.5, 0, 704.4, -90, 0, -90]` — nonzero W/R. If a backup of
   one ever poses wrong (likely rotated 90°), this is where to look. No
   F100iA backup has been seen; unvalidated tier either way.
4. **The rail/mount lane is not in ROADMAP.** The 2026-07-18 fleet sweep
   found rail-mounted 210L robots (carriage/J7 unmodeled) and one
   odd-mount family that the calibration gate refuses individually —
   recorded then as "excluded types = future ROADMAP lane", but ROADMAP
   carries no rail/carriage/mount line (grep 2026-08-01: nothing). Either
   land the lane (model the carriage as a prismatic base offset from J7 +
   the mount as a base frame) or record that the per-robot refusal *is*
   the permanent answer. Same broken-paper-trail shape as
   backup-capture.md §9 item 2 — which got its ROADMAP line in `0372b09`,
   so the precedent is to land it.
5. **`proj3d.js` is the only in-scope file with zero direct tests** (§8).
   Its math is pure and trivially testable (basis orthonormality, ortho ↔
   unproject round-trip, prism topology, perspective center-invariance);
   a small tracked `test_proj3d`-style suite would also pin the third WPR
   implementation against the other two.
6. **The table's generator is not in the repo.** `kinematics_builtin.py`
   (~158 KB of generated Python) was produced from a Roboguide install's
   def library by a session script that never landed; regenerating means
   re-importing the defs and re-writing the emitter (mind the
   `repr`-not-`json.dumps` trap, §7), then hand-grafting the five
   `validated` blocks. A `tools/` script would make the table
   reproducible; until then treat the file as data with provenance, not
   build output.
7. **Group ≥2 never poses** (`api.py:1449`, `groups[0]`). Fine while the
   arm is the group-1 robot everywhere seen, but a positioner-heavy cell
   would show group-filtered zones with an arm that ignores the
   positioner. Recorded, not a defect today.
8. **`utool_num` element semantics are assumed** (§4): elements carrying a
   tool-frame number are excluded from drawing on the reading that their
   positions compose through a taught tool. Pairing one against a pendant
   (a backup with a tool-frame-attached DCS element) would settle it and
   maybe un-exclude them.

---

## What this pass could not verify

The honest tail, per the template. No hardware was dialed; everything
below is recorded-and-consistent, not re-proven.

- **The original accuracy constants** — 0.13 mm / 0.005° on the testbed,
  <0.15 mm / ≤0.03° across five families, and the brute-force margin (the
  runner-up convention missing by ~105 mm — no ambiguity) — are recorded
  in `kinematics.py:3-5` / `roboguidedef.py:15-18` and the project's
  session history. Today's re-measure confirms **one family on two
  snapshots** (0.011–0.047 mm); the other four validated families have no
  robot in the pinned fixture set, so their residuals stand as recorded.
- **The +23.0 mm plate constants** (210F-IF, 100F-IF families) — recorded
  2026-07-18; today's pins are 280L only (+9.955/+10.071 measured against
  its +10.06 record).
- **The rail/mount refusals** (§4, §9 item 4) — recorded from the fleet
  sweep; no rail robot is pinned, so the refusal was not re-triggered.
- **The link-frame convention for link-attached model elements** — the
  approx-dash honesty exists *because* nobody has verified it; needs a
  robot with a known link-mounted shape and a pendant.
- **The viewport's rendered behavior** — cannot be verified by reading,
  and currently is not verified by anything else either (§8). Listed here
  too because it is this pass's largest single unknown.
- **`$MODE = 2`, `$SIZE[2]`, `$DCSS_TUIRO`/`$DCSS_TUIZN`** — parsing.md's
  closing owns these; unchanged, still unverified, still honest `?`s.
- **The `.rmd`-derived cross-checks** behind the model provenance (§2) —
  the excluded toolkit was not examined by design; the claim that it
  matched the `.def` kinematics numerically is the build session's record.
- **The exotic chains** (the delta DR-3iB, the M-421iA two-axis, the
  7-axis type, both F100iA entries) — wellformed and runnable by test,
  never posed against a real controller.
