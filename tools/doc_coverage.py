"""Which shipped files does no subsystem document claim yet?

The documentation pass answers "is it covered?" by eye, which stops scaling
somewhere around the third document. This makes it a command, the same way
tools/update_inventory.py made the line counts a command.

Each doc in docs/subsystems/ opens with a machine-readable block:

    Covers: path/one.py, path/two.js,
    path/three.py
    (N files)

Paths are repo-relative, comma-separated, and may wrap across lines. The
trailing "(N files)" is optional and is checked against the actual count.

SCOPE - only what ships. A file earns a document by running when somebody uses
the app, so this counts src/backupviewer/ and nothing else: tests, docs, build
config, tools/ CLIs, the LibraryImporter companion and the git-excluded .rmd
research toolkit are all deliberately out.

OVERLAP IS NOT AN ERROR. A layer doc and a domain doc can both claim a file -
parsers/dcszones.py is parsed in parsing.md and used in 3d-viewer.md, and the
second links rather than restates. Overlaps are reported so they stay
deliberate; only a claim on a file that does not exist is a failure, because
that is the doc drifting away from the tree.

    python tools/doc_coverage.py            report
    python tools/doc_coverage.py --check    exit 1 on a claim that cannot resolve
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "subsystems"
SHIPPED = ROOT / "src" / "backupviewer"

# things under src/ that are not source
IGNORE_SUFFIX = {".pyc", ".pyo"}
IGNORE_PARTS = {"__pycache__"}


def shipped_files():
    out = []
    for p in SHIPPED.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in IGNORE_SUFFIX or IGNORE_PARTS & set(p.parts):
            continue
        out.append(p.relative_to(ROOT).as_posix())
    return sorted(out)


def claims():
    """doc name -> [repo-relative paths]"""
    found = {}
    for doc in sorted(DOCS.glob("*.md")):
        text = doc.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^Covers:(.*?)(?:^\((\d+) files?\)\s*$|^\s*$)",
                      text, re.M | re.S)
        if not m:
            found[doc.name] = None          # no declaration at all
            continue
        body = re.sub(r"[`*]", " ", m.group(1))
        paths = [q.strip() for q in body.replace("\n", " ").split(",") if q.strip()]
        declared = int(m.group(2)) if m.group(2) else None
        found[doc.name] = (paths, declared)
    return found


def expand(paths, ship):
    """A path ending in / claims every shipped file beneath it.

    28 theme JSONs, a font and three handshake blobs are claimed as groups, not
    named one by one - listing them individually would be a maintenance burden
    that buys nothing and goes stale the first time a theme is added."""
    out, unresolved = [], []
    for p in paths:
        if p.endswith("/"):
            under = [s for s in ship if s.startswith(p)]
            if under:
                out.extend(under)
            else:
                unresolved.append(p)
        else:
            out.append(p)
    return out, unresolved


def main():
    check = "--check" in sys.argv
    ship = shipped_files()
    by_doc = claims()

    undeclared = [d for d, v in by_doc.items() if v is None]
    owner, missing = {}, []
    for doc, v in by_doc.items():
        if v is None:
            continue
        paths, declared = v
        expanded, unresolved = expand(paths, ship)
        for p in unresolved:
            missing.append((doc, f"{p} (directory claims nothing)"))
        for p in expanded:
            if p not in ship:
                missing.append((doc, p))
            owner.setdefault(p, []).append(doc)
        # the declared count is against what the author WROTE, not the expansion
        if declared is not None and declared != len(paths):
            missing.append((doc, f"(declares {declared} files, lists {len(paths)})"))

    unclaimed = [p for p in ship if p not in owner]
    overlaps = {p: d for p, d in owner.items() if len(d) > 1}

    print(f"docs: {len(by_doc)}   shipped files: {len(ship)}   "
          f"claimed: {len(ship) - len(unclaimed)}   unclaimed: {len(unclaimed)}")
    if undeclared:
        print(f"\nNO 'Covers:' BLOCK ({len(undeclared)}) - invisible to this tool:")
        for d in undeclared:
            print(f"   {d}")
    if missing:
        print(f"\nBROKEN CLAIMS ({len(missing)}) - a doc names something that is not a shipped file:")
        for d, p in missing:
            print(f"   {d}: {p}")
    if overlaps:
        print(f"\noverlaps ({len(overlaps)}) - fine when one doc links the other, "
              f"a duplicate when it restates:")
        for p, d in sorted(overlaps.items()):
            print(f"   {p}  <- {', '.join(d)}")
    if unclaimed:
        print(f"\nSTILL UNDOCUMENTED ({len(unclaimed)}):")
        for p in unclaimed:
            print(f"   {p}")
    else:
        print("\nevery shipped file is claimed by a subsystem document.")

    # a gap is expected work; a broken claim or a silent doc is drift
    bad = len(missing) + len(undeclared)
    if check and bad:
        print(f"\n--check: {bad} problem(s)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
