"""Diff two backups' already-built payloads into a changes-only report.

Every function returns {"rows": [Row...], "counts": {added, removed, changed},
"truncated": bool} where Row = {"kind", "name", "a", "b"} - ALWAYS both sides,
so the UI can show robot A's value next to robot B's (or "non-existent")
without any git literacy. Counts are computed before the row cap so
truncation stays honest. IO signal STATES are deliberately excluded - two
snapshots of a running cell differ on hundreds of points that mean nothing
(user-confirmed). Mastering is not diffed but AUDITED: identical master
counts between two physical robots is the red flag (cloned mastering data).
"""
from __future__ import annotations

import difflib

MAX_ROWS = 500

_AXES = ("x", "y", "z", "w", "p", "r")


def align_program_lines(a_body: list[dict], b_body: list[dict]) -> dict:
    """Line-align two TP program bodies for a side-by-side diff view.

    Input: parse_ls_program 'body' lists [{n, text}]. Output rows keep BOTH
    sides per visual row: kind = same | change | a_only | b_only.
    """
    a_texts = [l["text"] for l in a_body]
    b_texts = [l["text"] for l in b_body]
    sm = difflib.SequenceMatcher(a=a_texts, b=b_texts, autojunk=False)
    rows: list[dict] = []
    stats = {"same": 0, "change": 0, "a_only": 0, "b_only": 0}

    def row(kind, ai, bi):
        stats[kind] += 1
        rows.append({
            "kind": kind,
            "a_n": a_body[ai]["n"] if ai is not None else None,
            "a_text": a_body[ai]["text"] if ai is not None else None,
            "b_n": b_body[bi]["n"] if bi is not None else None,
            "b_text": b_body[bi]["text"] if bi is not None else None,
        })

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                row("same", i1 + k, j1 + k)
        elif op == "replace":
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                ai = i1 + k if i1 + k < i2 else None
                bi = j1 + k if j1 + k < j2 else None
                row("change" if ai is not None and bi is not None
                    else ("a_only" if ai is not None else "b_only"), ai, bi)
        elif op == "delete":
            for k in range(i1, i2):
                row("a_only", k, None)
        elif op == "insert":
            for k in range(j1, j2):
                row("b_only", None, k)
    return {"rows": rows, "stats": stats}


def _comment_only_row(r: dict) -> bool:
    """A diff row whose present side(s) are only ! comments (or blank)."""
    for t in (r.get("a_text"), r.get("b_text")):
        if t is None:
            continue
        s = t.strip()
        if s and not s.startswith("!"):
            return False
    return True


def count_program_line_diffs(a_body: list[dict], b_body: list[dict],
                             ignore_comments: bool = False) -> int:
    out = align_program_lines(a_body, b_body)
    n = 0
    for r in out["rows"]:
        if r["kind"] == "same":
            continue
        if ignore_comments and _comment_only_row(r):
            continue
        n += 1
    return n


def _row(kind: str, name: str, a: str = "", b: str = "") -> dict:
    return {"kind": kind, "name": name, "a": str(a), "b": str(b)}


def finish(rows: list[dict]) -> dict:
    counts = {"added": 0, "removed": 0, "changed": 0}
    for r in rows:
        counts[r["kind"]] += 1
    return {
        "rows": rows[:MAX_ROWS],
        "counts": counts,
        "truncated": len(rows) > MAX_ROWS,
    }


def diff_kv(a: dict, b: dict, fields: list[tuple[str, str]]) -> dict:
    """fields: [(key, label)] - changed values only."""
    rows = []
    for key, label in fields:
        va, vb = a.get(key, ""), b.get(key, "")
        if va != vb and (va or vb):
            rows.append(_row("changed", label, va or "—", vb or "—"))
    return finish(rows)


def diff_options(a: list[dict], b: list[dict]) -> dict:
    am = {o["feature"]: o["ord_no"] for o in a}
    bm = {o["feature"]: o["ord_no"] for o in b}
    rows = []
    for f in sorted(set(am) | set(bm)):
        if f not in bm:
            rows.append(_row("removed", f, am[f]))
        elif f not in am:
            rows.append(_row("added", f, "", bm[f]))
    return finish(rows)


def diff_io(a: dict, b: dict, ignore_comments: bool = False,
            ignore_values: bool = False) -> dict:
    """Comments + physical assignments; states excluded by design.
    'values' here = rack/slot/port assignment; 'comments' = signal names."""
    def keyed(io):
        return {f"{s['type']}[{s['index']}]": s for s in io["signals"]}

    def describe(s):
        c = s["comment"] or "—"
        if s.get("rack") is not None:
            c += f"  (r{s['rack']} s{s['slot']} p{s['port']})"
        return c

    am, bm = keyed(a), keyed(b)
    rows = []
    for k in sorted(set(am) | set(bm), key=_signal_sort_key):
        sa, sb = am.get(k), bm.get(k)
        if sb is None:
            if sa["comment"] and not ignore_comments:
                rows.append(_row("removed", k, describe(sa)))
        elif sa is None:
            if sb["comment"] and not ignore_comments:
                rows.append(_row("added", k, "", describe(sb)))
        else:
            asg_a = (sa.get("rack"), sa.get("slot"), sa.get("port"))
            asg_b = (sb.get("rack"), sb.get("slot"), sb.get("port"))
            cmt_diff = sa["comment"] != sb["comment"] and not ignore_comments
            asg_diff = asg_a != asg_b and not ignore_values
            if cmt_diff or asg_diff:
                rows.append(_row("changed", k, describe(sa), describe(sb)))
    return finish(rows)


def _signal_sort_key(k: str):
    t, _, rest = k.partition("[")
    try:
        return (t, int(rest.rstrip("]")))
    except ValueError:
        return (t, 0)


def diff_scalar_registers(a: list[dict], b: list[dict], prefix: str,
                          ignore_comments: bool = False,
                          ignore_values: bool = False) -> dict:
    am = {r["index"]: r for r in a}
    bm = {r["index"]: r for r in b}

    def desc(r):
        v = "—" if r["value"] in (None, "") else str(r["value"])
        return v + (f"  '{r['comment']}'" if r.get("comment") else "")

    def empty(r):
        return not r.get("comment") and r["value"] in (None, "", 0)

    rows = []
    for i in sorted(set(am) | set(bm)):
        ra, rb = am.get(i), bm.get(i)
        name = f"{prefix}[{i}]"
        if rb is None:
            if not empty(ra):
                rows.append(_row("removed", name, desc(ra)))
        elif ra is None:
            if not empty(rb):
                rows.append(_row("added", name, "", desc(rb)))
        else:
            val_diff = ra["value"] != rb["value"] and not ignore_values
            cmt_diff = ra.get("comment", "") != rb.get("comment", "") and not ignore_comments
            if val_diff or cmt_diff:
                rows.append(_row("changed", name, desc(ra), desc(rb)))
    return finish(rows)


def _pos_desc(r) -> str:
    if r.get("kind") == "joint":
        return "J " + " ".join(f"{j:.3f}" if j is not None else "—" for j in r["joints"])
    if r.get("kind") == "cartesian":
        return " ".join(f"{ax}{r.get(ax, 0):.3f}" for ax in _AXES)
    return "uninit"


def _pos_equal(ra, rb) -> bool:
    if ra.get("kind") != rb.get("kind"):
        return False
    if ra.get("kind") == "joint":
        ja, jb = ra.get("joints", []), rb.get("joints", [])
        return len(ja) == len(jb) and all(
            (x is None and y is None) or
            (x is not None and y is not None and abs(x - y) < 0.0005)
            for x, y in zip(ja, jb)
        )
    if ra.get("kind") == "cartesian":
        return all(abs(ra.get(ax, 0) - rb.get(ax, 0)) < 0.0005 for ax in _AXES)
    return True


def diff_posreg(a: list[dict], b: list[dict], ignore_comments: bool = False,
                ignore_values: bool = False) -> dict:
    am = {(r["group"], r["index"]): r for r in a}
    bm = {(r["group"], r["index"]): r for r in b}
    rows = []
    for key in sorted(set(am) | set(bm)):
        ra, rb = am.get(key), bm.get(key)
        name = f"PR[{key[1]}] g{key[0]}"
        if rb is None:
            if ra["kind"] != "uninit":
                rows.append(_row("removed", name, _pos_desc(ra)))
        elif ra is None:
            if rb["kind"] != "uninit":
                rows.append(_row("added", name, "", _pos_desc(rb)))
        else:
            cmt_changed = (ra.get("comment", "") != rb.get("comment", "")
                           and not ignore_comments)
            pos_changed = not _pos_equal(ra, rb) and not ignore_values
            if pos_changed or cmt_changed:
                def label(r):
                    return (f"'{r['comment']}' " if r.get("comment") else "") + _pos_desc(r)
                if ra["kind"] == "uninit" and rb["kind"] != "uninit":
                    rows.append(_row("added", name, "", label(rb)))
                elif ra["kind"] != "uninit" and rb["kind"] == "uninit":
                    rows.append(_row("removed", name, label(ra)))
                else:
                    rows.append(_row("changed", name, label(ra), label(rb)))
    return finish(rows)


def diff_frames(a: dict, b: dict, ignore_comments: bool = False,
                ignore_values: bool = False) -> dict:
    rows = []
    for kind, label in (("tools", "UT"), ("frames", "UF")):
        groups = sorted(set(a.get(kind, {})) | set(b.get(kind, {})))
        for g in groups:
            am = {e["index"]: e for e in a.get(kind, {}).get(g, [])}
            bm = {e["index"]: e for e in b.get(kind, {}).get(g, [])}

            def desc(e):
                s = " ".join(f"{ax}{e.get(ax, 0):.3f}" for ax in _AXES) if not e.get("uninit") else "uninit"
                return (f"'{e['comment']}' " if e.get("comment") else "") + s

            def is_set(e):
                return e and not e.get("uninit") and any(e.get(ax) for ax in _AXES)

            for i in sorted(set(am) | set(bm)):
                ea, eb = am.get(i), bm.get(i)
                name = f"{label} {i} (g{g})"
                if is_set(ea) and not is_set(eb):
                    rows.append(_row("removed", name, desc(ea)))
                elif is_set(eb) and not is_set(ea):
                    rows.append(_row("added", name, "", desc(eb)))
                elif ea and eb:
                    pos_diff = not _pos_equal_frames(ea, eb) and not ignore_values
                    cmt_diff = (ea.get("comment", "") != eb.get("comment", "")
                                and not ignore_comments)
                    if (pos_diff or cmt_diff) and (is_set(ea) or is_set(eb)
                                                   or ea.get("comment") or eb.get("comment")):
                        rows.append(_row("changed", name, desc(ea), desc(eb)))
    return finish(rows)


def _pos_equal_frames(ea, eb) -> bool:
    return all(abs((ea.get(ax) or 0) - (eb.get(ax) or 0)) < 0.0005 for ax in _AXES)


def diff_programs(a: list[dict], b: list[dict]) -> dict:
    # -BCKED*- style system markers differ by timestamp on every backup; noise
    am = {p["name"].upper(): p for p in a if not p["name"].startswith("-")}
    bm = {p["name"].upper(): p for p in b if not p["name"].startswith("-")}

    def desc(p):
        bits = []
        if p.get("modified"):
            bits.append(str(p["modified"]).replace("T", " "))
        if p.get("line_count") is not None:
            bits.append(f"{p['line_count']} ln")
        if p.get("prog_size") is not None:
            bits.append(f"{p['prog_size']} B")
        if p.get("binary"):
            bits.append("binary")
        return " · ".join(bits) or "—"

    rows = []
    for name in sorted(set(am) | set(bm)):
        pa, pb = am.get(name), bm.get(name)
        if pb is None:
            row = _row("removed", name, desc(pa))
        elif pa is None:
            row = _row("added", name, "", desc(pb))
        elif (pa.get("modified"), pa.get("prog_size"), pa.get("line_count")) != \
                (pb.get("modified"), pb.get("prog_size"), pb.get("line_count")):
            # binary programs have no listing to line-diff, so a "change" is only
            # metadata (date/size) - pure noise. Only surface binary programs when
            # they're added/removed (handled above); skip binary-vs-binary changes.
            if pa.get("binary") or pb.get("binary"):
                continue
            row = _row("changed", name, desc(pa), desc(pb))
        else:
            continue
        # the program's own comment identifies it at a glance - carry it so the
        # UI can show it (it doesn't fit the narrow name column)
        comment = (pa or {}).get("comment") or (pb or {}).get("comment") or ""
        if comment:
            row["comment"] = comment
        rows.append(row)
    return finish(rows)


def diff_macros(a: list[dict], b: list[dict]) -> dict:
    am = {m["name"]: m for m in a}
    bm = {m["name"]: m for m in b}

    def desc(m):
        s = m["prog_name"]
        if m.get("assign_type"):
            s += f"  {m['assign_type']}[{m.get('assign_id') or ''}]"
        return s

    rows = []
    for name in sorted(set(am) | set(bm)):
        ma, mb = am.get(name), bm.get(name)
        if mb is None:
            rows.append(_row("removed", name, desc(ma)))
        elif ma is None:
            rows.append(_row("added", name, "", desc(mb)))
        elif (ma["prog_name"], ma.get("assign_type"), ma.get("assign_id")) != \
                (mb["prog_name"], mb.get("assign_type"), mb.get("assign_id")):
            rows.append(_row("changed", name, desc(ma), desc(mb)))
    return finish(rows)


def diff_variables(a_flat: dict[str, str], b_flat: dict[str, str],
                   ignore_comments: bool = False, ignore_values: bool = False) -> dict:
    """Diff two KAREL programs' flattened variables. For variables, the name
    is the identity ('comment') and the value is the value: ignore_comments
    skips added/removed variables; ignore_values skips value-only changes."""
    rows = []
    for key in sorted(set(a_flat) | set(b_flat)):
        av, bv = a_flat.get(key), b_flat.get(key)
        if av is None:
            if not ignore_comments:
                rows.append(_row("added", key, "", bv))
        elif bv is None:
            if not ignore_comments:
                rows.append(_row("removed", key, av))
        elif av != bv and not ignore_values:
            rows.append(_row("changed", key, av, bv))
    return finish(rows)


def count_variable_diffs(a_flat: dict[str, str], b_flat: dict[str, str],
                         ignore_comments: bool = False, ignore_values: bool = False) -> int:
    return sum(diff_variables(a_flat, b_flat, ignore_comments, ignore_values)["counts"].values())


def audit_mastering(a: list[dict], b: list[dict]) -> dict:
    """Two different physical robots MUST have different master counts.
    Identical counts mean someone cloned mastering data - that's the alarm,
    not the difference."""
    am = {g["group"]: g for g in a}
    bm = {g["group"]: g for g in b}
    rows = []
    checked = 0
    for g in sorted(set(am) & set(bm)):
        ca = am[g]["master_counts"]
        cb = bm[g]["master_counts"]
        if not ca or not cb:
            continue
        checked += 1
        if ca == cb:
            counts_str = " ".join(str(c) for c in ca)
            rows.append(_row("alert", f"group {g} master counts IDENTICAL",
                             counts_str, counts_str))
    return {
        "rows": rows[:MAX_ROWS],
        "counts": {"alert": len(rows)},
        "truncated": False,
        "ok": not rows,
        "checked_groups": checked,
        "note": ("identical master counts on two different robots usually means "
                 "cloned mastering data - the robot is NOT actually mastered"),
    }
