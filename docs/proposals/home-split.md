# Splitting `tabs/home.js`

Investigation only — no code was changed. Written 2026-07-28 against
`home.js` at 2,277 lines (working tree, one uncommitted comment hunk).
**Re-anchored 2026-07-31 against `main` @ `63f7196`** (`home.js` at 2,295
lines): every line range, count and inline `file:line` cite below was
re-derived against that revision. The two UI-chrome batches in between
moved the library's action row into the topbar's toolbar slot and put the
selection actions behind the functions… menu — the analysis and the
phasing stand unchanged.

> **Status.** Phase 0's first item is done (`perf_probe` runs again, and is
> now collected). Its other two items — a flow probe for batch backup and for
> tidy — are **not**, and they are the real gate: those are exactly the two
> things phases 3–4 move, and they have no UI coverage at all today.
>
> Read this alongside `../INVENTORY.md`. This file is the deeper answer for
> the library screen; the inventory is the breadth map.

---

## 1. What is actually in there

Boundaries taken from the function map, so the line counts add up to the
whole file.

| Lines | n | Responsibility | Kind |
|---|---:|---|---|
| 1–37 | 37 | module header + 24 module-level `_vars` | state |
| 38–124 | 87 | sort mode, lens mode, the three comparators | state |
| 125–166 | 42 | the two `BV.libTree` instances (`_tree`, `_camTree`) | shell |
| 167–243 | 77 | `render` / `loadLibrary` / `watchScanProgress` | shell |
| 244–422 | **179** | `buildLibraryHead` — the action row in the topbar's toolbar slot, functions… menu included | shell |
| 423–511 | 89 | `refresh` / `rerenderFromCache` / scroll anchoring | shell |
| 512–541 | 30 | per-lens scroll memory, hidden-toggle label | shell |
| 542–599 | 58 | the ★ favorites strip | lens (backup) |
| 600–667 | 68 | `renderTree` — the lens fork lives here | shell |
| 668–977 | **310** | robot row + row ⋯ menu + inline notes editor + `openRobot` | lens (backup) |
| 978–1148 | **171** | multi-cam: `renderCamGrid`, `camTile`, `startCamRefresh` | lens (cam) |
| 1149–1228 | 80 | selection helpers, `syncToolbar`, `BV.libActions`, hide | state |
| 1229–1530 | **302** | tidy: fix-names, merge, move, three confirm-batch modals | flow |
| 1531–1663 | **133** | batch FTP backup, shared-password prompt, row progress | flow |
| 1664–2004 | **341** | add/edit robot modal + its private form kit | flow |
| 2005–2042 | 38 | scan-progress bar helpers (shared with discover) | flow |
| 2043–2295 | **253** | discover-on-network, two-step | flow |

Rolled up:

- **flows: ~1,067 lines (47%)** — dialogs and job launchers
- **shell: ~485 lines (21%)** — mount, fetch, head, repaint, scroll
- **lens rendering: ~539 lines (23%)** — robot rows, favorites, cam tiles
- **shared state: ~204 lines (9%)**

---

## 2. Does the shell / lens / flow split match the code?

**Half of it does, and it is the half you put second.**

### The flow half is right, and the code already admits it

`home.js` exports four things purely because code *outside* the view needs
code that lives *inside* it:

```
BV.libActions          -> manage_ui.js calls selected/fixNames/merge/moveTo/autoLink
BV.promptSharedPassword-> manage_ui.js retry path
BV.openLocation        -> overview.js "open location" button
BV.home                -> router.js, for the topbar cubes
```

`manage_ui.js` is a 271-line modal that lives outside `home.js`, is opened
from a button that is *not* selection-gated, and whose entire job is to
drive four flows it does not own. The comment on `BV.libActions` says it
out loud: *"the manage-backups modal drives the selected-robot flows
without owning selection state or the flows themselves."*

That is a subsystem with its view still attached. The flows depend on the
view for exactly two things — a list of robots in, and a `refresh()` out —
and on nothing else in the file. None of them touch `_libWrap`, the tree
DOM, the scroll anchor, or either `libTree` instance.

The editing workspace you cite as precedent is the same move already made
once: `#edit` is a shell screen with `workspace.js` (383 lines) holding the
machinery behind it.

### The lens half is the weakest seam in the file

The two lenses are not two screens. They are one screen with a fork in
`renderTree` at line 638. They share: the fetched listing (`_lastData`),
the filter box *and its match counter*, the sort comparator, the hidden
toggle and its lens-scoped count, the scroll-anchor mechanism, the ★
concept (a starred robot floats its cameras in the cam lens too), and the
mount guard. What is genuinely cam-only is 170 lines plus a `libTree`
instance.

Split by lens and you get a ~170-line `home-multicam.js` and a still-2,100
line `home.js`, having pushed six pieces of shared state into a new owner
that both files now reach through. That is a worse file for a worse reason.

### Where the seams actually are, biggest first

1. **Flows out** (~1,079 lines, 47%). Four subsystems: `libtidy`,
   `libbackup`, `libedit` (add/edit + the form kit), `libdiscover`.
   Invoked by buttons, own no view state.
2. **Row renderers out** (~430 lines). `robotRow` + its ⋯ menu + the
   inline notes editor is 306 lines and is a *component*, not a screen —
   it is already called from two places (the tree and the favorites strip)
   with an `opts` flag to tell them apart.
3. **Lens split** (~230 lines moved). Do it last, if at all. After 1 and 2,
   `home.js` is ~750 lines and the lens fork is eight lines in `renderTree`.

So: your instinct that the flows do not belong to a view is right and is
the whole win. The shell/lens framing is the part I would drop.

---

## 3. State that genuinely spans the lenses, and who should own it

24 module-level `_vars`. Grouped by what would have to happen to each:

**Owned by the shell (both lenses read it) — 8 vars**

| var | refs | why it is shared |
|---|---:|---|
| `_libWrap` | 34 | the mounted container *and* the "am I still on screen?" guard — see §5 |
| `_tslot` | 5 | the topbar's toolbar slot — the action row and selection count render there now, so both lenses (and `syncToolbar`) reach it |
| `_lastData` / `_robots` | 16 | one fetch feeds both lenses; the cam lens reads the *full* list (unfiltered) to resolve linked-robot names and stars |
| `_filter` / `_filterBox` | 17 | one box; both lenses write its match counter via `setCount` |
| `_showHidden` / `_showHiddenBtn` | 11 | one toggle, but the *count* it displays is lens-scoped (`renderTree:632`) |
| `_sortMode` / `_viewMode` | 10 | `_viewMode` is already public API (`BV.home`, read by `router.js`) |

**Owned by the shell but per-lens by design — 3 vars**

`_lensScroll`, `_renderedLens`, and the `ANCHOR_SEL` mechanism. This is
already correct and generic: `scrollAnchor`/`restoreAnchor` key on
`data-robot-id` and match `.lib-robot, .cam-tile` — a third lens costs one
selector. Do not let a split fragment this; the comment records that
losing it landed users *thousands of rows* off at plant scale.

**Owned by selection — 3 vars**

`_cl` (the shared `BV.checklist`), `_visibleRobots`, and `syncToolbar`.
`_visibleRobots` is in *render order*, not cache order, because shift-click
ranges must match what the user sees (`renderTree:659`). The cam lens
deliberately empties it. This trio is a unit and should move together into
a `libselection` module that both the shell and the flows read — it is
already half-exposed as `BV.libActions.selected()`.

**Per-paint caches — 2 vars**

`_liveTargets` and `_camCounts` are built once per tree paint precisely
because per-row computation was n² at plant scale (`renderTree:582`). A
split that moves `robotRow` out must pass these *in*, not recompute them.

**Genuinely local — 8 vars**

`_tree`, `_camTree`, `_camTimer`, `_camRobotNames`, `_favOpen`,
`_lastAbsorbMsg`, `_warnedTruncated`, `_hadActiveJobs`.

---

## 4. What must become a shared component first

This is the `cvxremote.js` / `mtxremote.js` lesson. Those two files build
the *identical* five-button bar — reload / open in window / phone /
fullscreen / close — from two copies of the same code. The cost is visible
right now in this very working tree: the `BV.icon("phone")` change had to
be made twice, and `ui_cvxremote_probe.py` has a check named
`cvx.bar_matches_matrox` — a test whose entire job is to notice when the
copies drift. **They were split before the shared bar existed.** Do not
repeat that here.

Extract these **before** moving anything:

| # | Component | Why, and where the copies already are |
|---|---|---|
| 1 | `BV.emptyState(msg)` | `.empty-lib` markup is hand-built 4× in `home.js` alone (576–643, 950–1119). The composition audit counts ~32 sites repo-wide. Cheapest and lowest-risk. |
| 2 | `BV.mounted(el)` guard | `if (!X \|\| !document.body.contains(X)) return;` appears 5× and is load-bearing: the `rerenderFromCache` comment records a bug where a detached tree reached into a `#view` owned by another tab. If each split file re-implements it, one of them will get it wrong. |
| 3 | `BV.libRow` meta line | `lib-robot-name` + `lib-robot-meta` with `·` separators is built by hand in **both** `robotRow` and `camTile` — a two-copy drift that already exists *inside one file*. |
| 4 | `BV.confirmBatch` | `confirmRenameBatch` (1310), `confirmMergeBatch` (1432), `confirmSingleMerge` (1483) are one shape with three bodies. Splitting tidy out without this ships three copies into a new file. |
| 5 | `BV.formModal` / combo field | `inp` / `field` / `comboField` / `knownPlants` / `knownLines` (1646–1705) is a private 60-line form kit used by `editRobotModal` *and* discover's step two. Already named as a TIER2 pick in the composition audit. |
| 6 | scroll anchor → beside `BV.persistScroll` | Already generic. It just lives in the wrong file. |

Rule of thumb for this work: **if two of the target files would both need
it, it is a primitive, and it gets extracted in its own commit, before the
move.**

---

## 5. Risk: what could silently break

Ranked by "would a probe catch it?"

**Would NOT be caught today**

- **Batch FTP backup has zero UI coverage.** No probe check anywhere
  exercises `startLineBackup`, `promptSharedPassword`, `renderRowProgress`,
  `reattachProgress` or `cancelAllBackups`. This is the flow that talks to
  live robots on a plant floor, and it is the least-tested thing in the
  file.
- **Tidy (fix-names / merge / move) has zero UI coverage.** The *engine*
  is well covered — `test_library_relocate.py` is 824 lines — but nothing
  drives the modals that feed it. A split could break the flow that
  collects the input while every engine test stays green.
- **The row ⋯ menu and `openRobot`** are only partly covered.
- **Double-subscription.** `BV.state.on("library-dirty")` and
  `BV.state.on("jobs")` are registered at module scope (389, 398). If a
  split file also subscribes, you get two refreshes per event — invisible
  on a small library, a repeated full rescan at plant scale.
- **The n² regression.** Losing `_liveTargets` / `_camCounts` as
  once-per-paint caches degrades quietly: correct output, plant-scale
  freeze. `perf_probe.py` is the guard for exactly this — it was hung and
  unrunnable when this was written, and is green as of `871d3f0` (see below).

**Would be caught**

`ui_batch_probe.py` carries ~68 library checks — 44 `cam.*`, 17 `fav.*`,
5 `home.*` — plus 13 `notes.*`. Lens flipping, per-lens scroll memory,
hidden-count scoping, the favorites strip's dual-render selection sync,
cam tile lifecycle and the inline notes editor are all genuinely pinned.
The lens and favorites work is the *safest* to move, which is another
reason it is not where the value is.

### Coverage that has to exist first

1. ~~**Fix `perf_probe.py`.**~~ **DONE — `871d3f0`.** It had been hanging
   forever: `main()` passed `window` as the `args` argument to
   `webview.start()` while its callback takes none, so pywebview raised
   `TypeError` in the GUI thread, the probe body never ran and the window was
   never destroyed. Now green at its full 2400 rows in 9.2s with every budget
   met (editor_open 50ms of 80, keystroke 10ms of 25, shift_range 184ms of
   800) — those are the numbers a split must not regress. It is also
   *collected* now (`da10c92`), so it cannot silently rot again.
2. **A flow probe.** Batch backup and tidy each need a hidden-window probe
   with the endpoints stubbed — assert the modal collects the right spec
   and calls the right endpoint, not that the backup works. **Still open,
   and it is the real gate on phases 3–5.**
3. **A double-subscription assertion.** Count `library-dirty` / `jobs`
   handlers after a remount; assert one each. **Still open.**

---

## 6. Phased sequence

Every phase is independently revertible and leaves a working app. Nothing
here needs the next phase to be worth doing.

| Phase | Change | Net effect on `home.js` | Revert |
|---|---|---|---|
| **0** | ~~Fix `perf_probe.py`~~ (done, `871d3f0`); add the batch-backup + tidy flow probes | 0 | n/a — pure safety net |
| **1** | Extract `BV.emptyState` and `BV.mounted` (components 1–2) | −40 | one commit |
| **2** | Extract `BV.confirmBatch` and `BV.formModal` (components 4–5) | −120 | one commit |
| **3** | Move tidy → `libtidy.js`, behind the existing `BV.libActions` façade | −250 | the façade already exists; revert is a file move |
| **4** | Move batch backup → `libbackup.js` (keeps `BV.promptSharedPassword`) | −130 | as above |
| **5** | Move add/edit + discover → `libedit.js` / `libdiscover.js` | −560 | as above |
| **6** | Extract `BV.libRow` (component 3), then move rows → `librow.js` | −380 | as above |
| **7** | *Only if it still looks worth it:* split the cam lens | −200 | as above |

After phase 5, `home.js` is ~1,100 lines and is a shell. After phase 6 it
is ~750. Phase 7 buys ~200 lines and costs a shared-state owner — my
recommendation is to stop at 6 and reassess.

**Phase 0 is not optional.** Phases 3–5 move the two things with no UI
coverage at all. Doing them against the current safety net means trusting
a careful read, and the whole point of this pass was to stop doing that.

---

## 7. Where I think you are wrong, plainly

- "home should be a shell … with each lens its own script" — the lens
  split is real but it is the *smallest* seam in the file and it is the
  one that forces shared state into a new owner. It is phase 7, not
  phase 1.
- "the job-launching flows … might belong to their own subsystems,
  invoked by buttons rather than implemented here" — this is right, it is
  47% of the file, and `manage_ui.js` + `BV.libActions` are the code
  already telling you so. This is the whole proposal.
- The thing neither of us listed: **`buildLibraryHead` is 179 lines of
  toolbar** that wires the action row to code all over the file. It is the
  natural *last* thing to move, because after phases 3–6 it becomes a
  declarative list of `{label, title, onClick: BV.libtidy.fixNames}` — and
  at that point it is not worth its own file.
