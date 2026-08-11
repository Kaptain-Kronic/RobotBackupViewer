"""Recount the derived numbers in docs/INVENTORY.md from the tree itself.

The map's `~lines` column, the subsystem totals table and the Scope line are
all DERIVED data living in a hand-maintained file - within days of writing,
23 rows had drifted. Descriptions take judgement; counts never should. Run
this after any batch lands:

    python tools/update_inventory.py            # rewrite in place
    python tools/update_inventory.py --check    # exit 1 if anything is stale

Counting rule: `~lines` is the NEWLINE COUNT of the file's bytes (what
`wc -l` prints). PowerShell's `Measure-Object -Line` counts text lines and
disagrees on files without a trailing newline - do not "fix" this to match
it. Binary rows (a `*NN KB*` size cell) are left alone; glob rows (the
theme packs) sum their matches; rows marked *(untracked, local-only)* that
are missing from this machine keep their old count with a warning, missing
TRACKED files are an error - the map is wrong and a human should look.
"""
from __future__ import annotations

import re
import sys
from glob import glob
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "INVENTORY.md"

ROW = re.compile(r"^\| `(?P<path>[^`]+)` \| (?P<lines>[^|]+) \| (?P<desc>.*) \| "
                 r"(?P<subsystem>[^|]+) \| (?P<status>[^|]+) \|$")
GLOB_SUFFIX = re.compile(r"\s*\(\d+ files\)$")


def newline_count(p: Path) -> int:
    return p.read_bytes().count(b"\n")


def main() -> int:
    check = "--check" in sys.argv
    lines = DOC.read_text(encoding="utf-8").split("\n")

    changed: list[str] = []
    warned: list[str] = []
    errors: list[str] = []
    # per-subsystem tallies over the map rows: [file_count, line_sum]
    subs: dict[str, list[int]] = {}
    total_files = 0
    total_lines = 0

    in_map = False
    for i, line in enumerate(lines):
        if line.startswith("## The map"):
            in_map = True
            continue
        if in_map and line.startswith("---"):
            in_map = False
        if not in_map:
            continue
        m = ROW.match(line)
        if not m:
            continue
        raw_path = m.group("path")
        cell = m.group("lines").strip()
        subsystem = m.group("subsystem").strip()
        local_only = "untracked, local-only" in m.group("desc")

        pattern = GLOB_SUFFIX.sub("", raw_path)
        n_files = 1
        if "*" in pattern:
            hits = sorted(glob(str(ROOT / pattern)))
            n_files = len(hits)
            count = sum(newline_count(Path(h)) for h in hits) if hits else None
            # keep the row's "(N files)" honest too
            new_path = f"{pattern} ({n_files} files)"
            if new_path != raw_path:
                line = line.replace(f"`{raw_path}`", f"`{new_path}`", 1)
                lines[i] = line
                changed.append(f"{pattern}: file count -> {n_files}")
        elif (ROOT / pattern).exists():
            count = newline_count(ROOT / pattern)
        else:
            count = None

        is_binary = cell.startswith("*")
        if count is None and not is_binary:
            if local_only:
                warned.append(f"{pattern}: not on this machine, count kept as-is")
                count_for_totals = int(cell) if cell.isdigit() else 0
            else:
                errors.append(f"{pattern}: tracked in the map but missing from disk")
                count_for_totals = 0
        elif is_binary:
            count_for_totals = 0
        else:
            count_for_totals = count
            if cell != str(count):
                lines[i] = ROW.sub(
                    lambda mm: f"| `{mm.group('path')}` | {count} | {mm.group('desc')} "
                               f"| {mm.group('subsystem')} | {mm.group('status')} |",
                    lines[i])
                changed.append(f"{pattern}: {cell} -> {count}")

        subs.setdefault(subsystem, [0, 0])
        subs[subsystem][0] += n_files
        subs[subsystem][1] += count_for_totals
        total_files += n_files
        total_lines += count_for_totals

    if errors:
        print("ERROR - the map names files that do not exist:")
        for e in errors:
            print("  " + e)
        return 1

    # ---- the subsystem totals table (section 1) ----
    text = "\n".join(lines)
    rows = sorted(subs.items(), key=lambda kv: -kv[1][1])
    table = ["| subsystem | files | ~lines |", "|---|---:|---:|"]
    table += [f"| {name} | {n} | {ln:,} |" for name, (n, ln) in rows]
    table += [f"| **total** | **{total_files}** | **{total_lines:,}** |"]
    text = re.sub(
        r"\| subsystem \| files \| ~lines \|\n\|[-:| ]+\|\n(?:\|.*\|\n)+",
        "\n".join(table) + "\n", text, count=1)

    # ---- the Scope line ----
    text = re.sub(r"\*\*Scope\.\*\* \d+ files / ~[\d,]+ lines\.",
                  f"**Scope.** {total_files} files / ~{total_lines:,} lines.",
                  text, count=1)

    if text != "\n".join(lines) or changed:
        if check:
            print(f"stale: {len(changed)} row(s) plus derived tables")
            for c in changed:
                print("  " + c)
            return 1
        DOC.write_text(text, encoding="utf-8", newline="\n")

    for w in warned:
        print("warn: " + w)
    print(f"{len(changed)} row(s) updated; {total_files} files / {total_lines:,} lines.")
    for c in changed:
        print("  " + c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
