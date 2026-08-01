"""Hidden-window probe pinning the JS FK twin to the Python reference.

kinematics.py's chain is the pendant-proven reference (0.13 mm / 0.005 deg);
fk.js re-implements it so the 3D tab can pose the arm per orbit tick without
a bridge call. Nothing held the two equal until this probe: it feeds the SAME
chains and joint sets to both and asserts every matrix cell of every joint
frame and the faceplate agrees within 1e-6 (mm / unit cell). That tolerance
is the invariant, not a frozen pose: f64 noise sits orders below it, any real
algorithmic divergence sits orders above.

Chains are a deterministic spread of the shipped builtin table (every
validated entry, the longest chain, a few evenly spaced others) plus the
synthetic parallel-link arm from test_kinematics - so NegDirection,
ParallelLink and the flange-dz path are always exercised no matter which
builtins ship. No backup is opened: BV.fk is pure math loaded at boot.

Run: python tests/ui_fk_probe.py
"""
import json
import sys
import time

from probeutil import FAILURES, check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_fk_probe_")

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402
from backupviewer.kinematics_builtin import BUILTIN  # noqa: E402
from backupviewer.parsers import kinematics, roboguidedef  # noqa: E402
from test_kinematics import _DEF  # noqa: E402

TOL = 1e-6


def pick_chains():
    """Deterministic spread - no randomness, so a failure reproduces."""
    keys = sorted(BUILTIN)
    chosen = {}
    for k in keys:
        if BUILTIN[k].get("validated"):
            chosen[k] = BUILTIN[k]["kin"]
    longest = max(keys, key=lambda k: (len(BUILTIN[k]["kin"]["joints"]), k))
    chosen.setdefault(longest, BUILTIN[longest]["kin"])
    step = max(1, len(keys) // 4)
    for k in keys[::step]:
        if len(chosen) >= 10:
            break
        chosen.setdefault(k, BUILTIN[k]["kin"])
    chosen["SYNTH-PARALLEL"] = roboguidedef.parse_def(_DEF)
    return chosen


def cases():
    """(name, kin, q, flange_dz) triplets per chain: home pose, a mixed pose,
    and a far pose with the flange-plate correction engaged."""
    base = [10.0, -20.0, 30.0, -40.0, 50.0, -60.0, 15.0, -25.0, 35.0]
    far = [90.0, 45.0, -30.0, 15.0, -75.0, 120.0, -10.0, 5.0, -5.0]
    out = []
    for name, kin in pick_chains().items():
        n = len(kin["joints"])
        out.append({"name": name, "kin": kin, "q": [0.0] * n, "dz": 0.0})
        out.append({"name": name, "kin": kin, "q": base[:n], "dz": 0.0})
        out.append({"name": name, "kin": kin, "q": far[:n], "dz": 23.0})
    return out


def max_cell_diff(a, b):
    """Largest |cell difference| across two {joints: [mat], faceplate: mat}."""
    mats_a = a["joints"] + [a["faceplate"]]
    mats_b = b["joints"] + [b["faceplate"]]
    if len(mats_a) != len(mats_b):
        return float("inf")
    worst = 0.0
    for ma, mb in zip(mats_a, mats_b):
        for ra, rb in zip(ma, mb):
            for va, vb in zip(ra, rb):
                worst = max(worst, abs(va - vb))
    return worst


def probe(window):
    try:
        ok = poll(window, "!!(window.BV && BV.fk && BV.fk.chain)")
        check("boot.fk_present", bool(ok))

        cs = cases()
        py = [kinematics.chain_frames(c["kin"], c["q"], c["dz"]) for c in cs]

        raw = js(window, """(function (cases) {
            return JSON.stringify(cases.map(function (c) {
                return BV.fk.chain(c.kin, c.q, c.dz);
            }));
        })(%s)""" % json.dumps(cs))
        got = json.loads(raw or "[]")
        check("fk.all_cases_returned", len(got) == len(cs),
              f"({len(got)}/{len(cs)})")

        for c, p, g in zip(cs, py, got):
            d = max_cell_diff(p, g)
            check(f"fk.twin.{c['name']}.dz{c['dz']:g}.q{c['q'][:2]}",
                  d < TOL, f"(max cell diff {d:.3e})")

        report()
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        window.destroy()


def main():
    lib = _TMP / "lib"
    lib.mkdir(parents=True)
    bv_settings.set_value("library_root", str(lib))

    api = Api()
    window = webview.create_window(
        "probe",
        url=str(resource_path("web/index.html")),
        js_api=api,
        width=1100,
        height=760,
        hidden=True,
    )
    api.bind(window)
    webview.start(probe, window, gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
