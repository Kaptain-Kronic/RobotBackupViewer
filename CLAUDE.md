# BackupViewer — how this codebase is built

Standing principles for every change, whoever (or whatever) writes it. The
goal of this file is that two people can build in parallel and their work
lands in the same style, the same layers, and the same quality bar — no
rework, no drift.

## What this app is

A desktop viewer for FANUC robot backups that plant techs trust when
troubleshooting real equipment. It runs offline on plant-floor PCs, ships as
a single ~13 MB exe, and reads evidence — it does not modify robots or
backups. Wrong data erodes trust worse than missing data, so **correctness
beats features, honesty beats polish, and lightweight is a feature**.

## The stack is locked

Python 3.13 **stdlib only** + pywebview (WebView2) + vanilla JS/CSS.
PyInstaller onefile. No Node, no build step, no bundler, no framework, no
CDN or network fetch of any kind — plant PCs are offline and the exe must be
the whole app.

- **No new runtime dependencies.** If a feature seems to need a library,
  redesign the feature. (The 3D zone viewer is a hand-rolled SVG projection
  for exactly this reason — zero bytes added, works under software
  rendering.)
- Any exception must survive all three: works fully offline · adds almost
  nothing to the exe · genuinely cannot be hand-rolled in a few hundred
  lines.

## Layers — where code goes

| Layer | Location | Rules |
|---|---|---|
| Parsers | `src/backupviewer/parsers/` | **Pure functions, text → dict.** No file I/O, no state, no UI concerns. This is what makes them testable and reusable. |
| Session | `session.py` | The backup's file index + lazy parse cache (`s.text(name)`, `s.cached(key, fn)`, `s.find(name)`). |
| API | `api.py` | Thin endpoints. Parse in parsers, cache via `s.cached`, fail with `ApiError`, return JSON-able dicts. Envelope (`{ok,data}/{ok,error}`) is handled for you. |
| Frontend | `web/js/` | Plain scripts, each an IIFE attaching to `window.BV`. Load order lives in `index.html` and matters. No imports, no modules. |
| Tabs | `web/js/tabs/` | `BV.tabs.push({id, label, render(view, toolbar, params)})`. Render runs fresh on an emptied slot each route. |

Cross-layer rules:

- JS never parses robot files; Python never renders HTML.
- Shared state lives **only** in `BV.state` (manifest, compare, settings,
  pub/sub). Per-tab memory: `BV.tabState(id)` — cleared per backup.
- The compare feature works through a **trailing `side="a"/"b"` parameter**
  on endpoints. Add new parameters *before* `side`, never after it, and
  never reorder — callers pass it positionally.
- Tab availability is data-driven: add your files to `TAB_REQUIREMENTS` in
  `parsers/__init__.py`; the tab lights up only when the backup has them.

## Composition — the core habit

Screens are assembled from small shared primitives in `web/js/components/`:
`BV.pill`, `BV.segmented`, `BV.card`, `BV.kv`, `BV.table` (static),
`BV.VTable` (virtualized: sort/resize/keys/persistence), `BV.MultiTable`,
`BV.checklist` (shift-ranges), `BV.collapsible`, `BV.menu`, `BV.modal`,
`BV.frameCard`, `BV.libTree`, `BV.dragReorder`, `BV.dcsDetail`,
`BV.proj3d`, `BV.persistScroll`, `BV.vsToggle` patterns.

The decision rule, in order:

1. **A primitive already does it** → use it.
2. **A primitive almost does it** → extend the primitive (an option, a
   callback), so every existing caller benefits. Never copy-paste-and-tweak
   a component into a private variant.
3. **It's genuinely new and will be used once** → keep it local to the tab.
4. **The same shape appears a third time** → promote it into a primitive.

This is why bugs stay sectioned (fix the primitive once, every screen
heals) and why features spread for free (add sorting to `VTable`, every
table gains it). A change that duplicates an existing behavior in new code
is wrong even if it works.

The same habit applies in Python: one parser per file format, shared
helpers promoted into `parsers/common.py` / `va.py`-style engines
(`record_tree` powers sysvars, KAREL vars, *and* MH valves — that's the
model).

## Honesty rules — the trust contract

- **Backups are read-only evidence.** Nothing in the app writes into a
  backup folder, ever.
- **Files are law.** The folder tree is the source of truth; sidecar JSON
  (`robot.json`, `backup.json`) carries identity + config only, never
  claims that contradict what's on disk. Presence = existence. A backup is
  only "complete" when its marker was written LAST (a job that dies mid-way
  must never look finished).
- **Parse what you can prove.** Map a raw value to a meaning only when it's
  been verified against a real controller/pendant. Unverified → show `?`
  plus the raw value. (The DCS `$MODE` map ships only the two values we
  ground-truthed; everything else says so.)
- **Diffs are two labeled columns** with `non-existent` for a missing side.
  Never a one-sided diff row.
- **Flag, never auto-fix.** The app points at problems; humans change
  robots.
- Empty/disabled entries are hidden by default but honestly listed behind a
  "show empty/disabled" toggle — never silently dropped.

## The plant-identifier firewall

The repo is public. **Nothing that identifies a real plant ever gets
committed**: no real robot or line names, plant names, IP addresses,
F-numbers, or real backup-tree paths — not in code, comments, tests,
fixtures, or commit messages.

- Fixture IPs come from TEST-NET (`192.0.2.x`). Fixture robots use fake
  families (`RB…`, `RC…` — e.g. `RB010R01B01`). Plants are
  `FakePlant`/`YourPlant`. **Before inventing a family prefix, check it is not
  in `robots.json` (untracked) — some obvious-looking two-letter prefixes are
  real lines, so never pick one from memory; `RB`/`RC` are known-safe.**
- Probes/tools that need the real tree stay untracked via
  `.git/info/exclude`. `SampleBackup/`, `robots.json`, and anything with
  live hardware references never enter git.
- **Before every push: grep the outgoing diff** for IP patterns and
  robot-name shapes and eyeball every hit.
- Passwords are prompted per run and held in memory only — never persisted,
  never logged.

## Gentle with live equipment

Discovery and backup touch production robots. Be conservative: throttled,
one connection at a time, timeouts everywhere, no retry storms. The viewer
never writes to a controller — write-back is a future, separately-gated,
human-in-the-loop tier, and it lands last.

## UI conventions

- **Theme through CSS variables only** (`--accent`, `--bg`, `--bg2`,
  `--edge`, `--sub`, `--ok/--warn/--error`, …). Never hardcode a color —
  derive from the variables (zone colors hue-rotate `--accent`, so all 28
  themes keep their character).
- Labels lowercase; status shown as pills (`ok-soft` / `warn` / `err`);
  disabled things render dim, not hidden.
- **Least input, most info.** A view scrolls only when it truly overflows —
  phantom scrollbars are bugs. Two-pane screens give each pane its own
  scroll. Every tab restores exactly how you left it (scroll, sort, folds,
  selection) — new UI wires `BV.tabState` / `BV.persistScroll` / VTable
  `stateKey` from day one.
- Keyboard reachable: digit keys switch tabs, `/` focuses the filter,
  `j/k` move, `esc` backs out. New screens respect the existing map in
  `keys.js`.

## Testing — the definition of done

- Parsers get **pytest** with synthetic fixtures that mirror real file
  shapes (self-contained, committed, identifier-clean).
- UI gets the **hidden-window probe** pattern: boot pywebview hidden,
  drive it with `evaluate_js`, assert on real DOM. Probes that point at
  real backups stay out of the repo.
- A feature is not done until it has run against a **real backup** and the
  numbers were checked against what the pendant/file actually says.
- Probe environment quirks your code must survive: no `requestAnimationFrame`,
  no native scroll events, pointer capture can fail (wrap in try/catch).

## Windows / WebView2 gotchas (paid for already)

- Persist settings via `set_setting` (→ `%APPDATA%`), **never**
  `localStorage` (private mode wipes it).
- Anything positioned from `getBoundingClientRect` must live outside the
  scaled body or its coordinates double-scale (see `BV.menu`).
- Never write a literal-NUL escape through a tool/JSON layer — build NULs
  programmatically (`String.fromCharCode(0)`, `bytes([0])`).
- A stray `*/` inside a JS block comment silently kills the whole file (the
  tab just vanishes).
- Robot files are `cp1252`, mixed line endings, sometimes masked
  (`********`) values — parsers tolerate all of it.

## Process

- **Check [ROADMAP.md](ROADMAP.md) before starting a feature** — it says
  what's decided, what's in progress, and which questions are still open.
  Claim a lane before building in it; two half-built versions of the same
  thing is the expensive kind of fun.
- **Small slices.** One coherent change per commit; the message says what
  and why, lowercase, like the existing history.
- Don't rename, move, or reformat files you aren't functionally changing —
  diff noise buries real changes.
- Prefer deleting code to adding it; prefer extending to duplicating;
  prefer the ASCII file that already exists to inventing new I/O.
- If the right implementation seems to require reworking something that
  already exists, stop and align first — agreeing on the seam beforehand is
  cheaper than reworking twice.
- Run before calling anything done: `python -m pytest tests -q`, the
  relevant probe, and the identifier/NUL sweep if you're committing.

## Quick reference — before you build X

| You need | Use |
|---|---|
| a table | `BV.table` (small/static) or `BV.VTable` (big/virtualized) |
| status chips | `BV.pill(text, variant)` |
| a mode switch / filter row | `BV.segmented` |
| collapsible anything | `BV.collapsible` (direct-child CSS rule for nesting) |
| a checkbox list with ranges | `BV.checklist` |
| a popup menu / picker | `BV.menu(anchor, items)` |
| a dialog | `BV.modal(title, body)` |
| key/value blocks | `BV.kv` / `BV.dcsDetail` (pendant-style) |
| frames/positions card | `BV.frameCard` |
| the library tree | `BV.libTree` |
| 3D/2D projection math | `BV.proj3d` |
| remembering UI state | `BV.tabState` / `BV.persistScroll` / VTable `stateKey` |
| a new file format | a pure parser in `parsers/` + a thin endpoint + `TAB_REQUIREMENTS` |
