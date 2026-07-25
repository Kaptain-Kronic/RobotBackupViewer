"""LibraryImporter core: parse -> plan -> seed, plus the destination sanity
checks. Fully synthetic - TEST-NET (192.0.2.x) IPs and made-up names only."""
import json

from libraryimporter import core


def _write_list(tmp_path, data, name="robots.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


GOOD = {
    "RBB01": {"010R01": "192.0.2.10", "020R01": "192.0.2.11"},
    "RCB02": {"005R01": "192.0.2.20"},
}


# ---- parse ---------------------------------------------------------------------

def test_parse_good_list_sorted_and_expanded(tmp_path):
    m = core.parse_source(_write_list(tmp_path, GOOD))
    assert m["ok"] and m["error"] == ""
    assert m["robots"] == 3 and m["warnings"] == []
    assert [ln["line"] for ln in m["lines"]] == ["RBB01", "RCB02"]
    rb = {r["robot"]: r for r in m["lines"][0]["robots"]}
    assert rb["010R01"]["full"] == "RB010R01B01"
    assert rb["020R01"]["ip"] == "192.0.2.11"
    assert m["lines"][1]["robots"][0]["full"] == "RC005R01B02"


def test_parse_skips_blanks_and_junk_with_warnings(tmp_path):
    m = core.parse_source(_write_list(tmp_path, {
        "RBB01": {"010R01": "", "020R01": "192.0.2.5", "030R01": 7},
        "notes": "a string, not a robot map",
        "EMPTY": {},
    }))
    assert m["ok"] and m["robots"] == 1
    assert [ln["line"] for ln in m["lines"]] == ["RBB01"]      # EMPTY dropped silently
    assert any("blank IP" in w for w in m["warnings"])
    assert any("notes" in w for w in m["warnings"])


def test_parse_skips_names_that_cannot_be_folders(tmp_path):
    m = core.parse_source(_write_list(tmp_path, {
        "RB/B01": {"010R01": "192.0.2.5"},              # a slash would change tree depth
        "RBB01": {"010R01 ": "192.0.2.6", "020R*1": "192.0.2.7"},
    }))
    assert m["ok"] and m["robots"] == 1
    r = m["lines"][0]["robots"][0]
    assert r["robot"] == "010R01" and r["full"] == "RB010R01B01"   # stray space trimmed
    assert any("illegal folder characters" in w for w in m["warnings"])


def test_parse_rejects_garbage(tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    assert core.parse_source(bad)["ok"] is False

    assert core.parse_source(_write_list(tmp_path, [1, 2], "list.json"))["ok"] is False
    assert core.parse_source(_write_list(tmp_path, {}, "empty.json"))["ok"] is False
    m = core.parse_source(_write_list(tmp_path, {"RBB01": {"010R01": ""}}, "allblank.json"))
    assert m["ok"] is False and "no robots" in m["error"]
    assert core.parse_source(tmp_path / "nope.csv")["ok"] is False   # no parser
    assert core.parse_source(tmp_path / "gone.json")["ok"] is False  # unreadable


def test_full_name_expansion_table():
    assert core.full_name("RBB01", "080R01") == "RB080R01B01"
    assert core.full_name("rbb01", "080r01") == "RB080R01B01"        # case folds
    assert core.full_name("RBB01", "RB080R01B01") == "RB080R01B01"   # already full
    assert core.full_name("LAB", "WELDER") == "WELDER"               # one-off passes raw
    assert core.full_name("RCB02", "005R01") == "RC005R01B02"


# ---- sidecar -------------------------------------------------------------------

def test_sidecar_is_schema2_with_no_identity_fields():
    sc = core.sidecar("192.0.2.9")
    # set-equality IS the guard: plant/line/robot must never creep in
    assert set(sc) == {"schema", "id", "model", "f_number", "ips", "ftp",
                       "notes", "aliases", "updated"}
    assert sc["schema"] == 2
    assert len(sc["id"]) == 32 and int(sc["id"], 16) >= 0
    assert sc["ips"] == ["192.0.2.9"]
    assert sc["ftp"] == {"user": "", "passive": True}


# ---- plan ----------------------------------------------------------------------

def test_plan_without_dest_everything_selectable(tmp_path):
    m = core.parse_source(_write_list(tmp_path, GOOD))
    p = core.plan(m, None)
    assert p["selectable"] == 3 and p["present"] == 0
    assert all(not r["present"] for ln in p["lines"] for r in ln["robots"])


def test_plan_marks_existing_folder(tmp_path):
    m = core.parse_source(_write_list(tmp_path, GOOD))
    dest = tmp_path / "FakePlant"
    (dest / "RBB01" / "RB010R01B01").mkdir(parents=True)
    p = core.plan(m, dest)
    rb = {r["robot"]: r for ln in p["lines"] for r in ln["robots"]}
    assert rb["010R01"]["why"] == "folder" and rb["010R01"]["present"]
    assert p["selectable"] == 2 and p["present"] == 1


def test_plan_marks_ip_claimed_under_a_different_name(tmp_path):
    m = core.parse_source(_write_list(tmp_path, GOOD))
    dest = tmp_path / "FakePlant"
    theirs = dest / "RBB01" / "010R01"          # short-named twin, same IP
    theirs.mkdir(parents=True)
    (theirs / "robot.json").write_text(json.dumps({"ips": ["192.0.2.10"]}), encoding="utf-8")
    p = core.plan(m, dest)
    rb = {r["robot"]: r for ln in p["lines"] for r in ln["robots"]}
    assert rb["010R01"]["why"] == "ip"
    assert rb["020R01"]["why"] == ""


def test_plan_marks_inlist_duplicate_ip_even_without_dest(tmp_path):
    m = core.parse_source(_write_list(tmp_path, {
        "RBB01": {"010R01": "192.0.2.10", "020R01": "192.0.2.10"},
    }))
    p = core.plan(m, None)
    rb = {r["robot"]: r for ln in p["lines"] for r in ln["robots"]}
    assert rb["010R01"]["why"] == "" and rb["020R01"]["why"] == "dup"
    assert p["selectable"] == 1 and p["present"] == 1


# ---- seed ----------------------------------------------------------------------

def _selection(model):
    return {ln["line"]: [r["robot"] for r in ln["robots"]] for ln in model["lines"]}


def test_seed_creates_the_skeleton(tmp_path):
    m = core.parse_source(_write_list(tmp_path, GOOD))
    dest = tmp_path / "FakePlant"
    dest.mkdir()
    res = core.seed(m, dest, _selection(m))
    assert res["created"] == 3 and res["skipped"] == 0 and res["errors"] == []
    for line, name, ip in [("RBB01", "RB010R01B01", "192.0.2.10"),
                           ("RBB01", "RB020R01B01", "192.0.2.11"),
                           ("RCB02", "RC005R01B02", "192.0.2.20")]:
        sc = json.loads((dest / line / name / "robot.json").read_text(encoding="utf-8"))
        assert sc["schema"] == 2 and sc["ips"] == [ip]
        assert not (dest / line / name / "robot.json.tmp").exists()
    assert {b["line"]: b["created"] for b in res["by_line"]} == {"RBB01": 2, "RCB02": 1}


def test_seed_rerun_skips_everything(tmp_path):
    m = core.parse_source(_write_list(tmp_path, GOOD))
    dest = tmp_path / "FakePlant"
    dest.mkdir()
    core.seed(m, dest, _selection(m))
    res = core.seed(m, dest, _selection(m))
    assert res["created"] == 0 and res["skipped"] == 3


def test_seed_honors_the_selection_subset(tmp_path):
    m = core.parse_source(_write_list(tmp_path, GOOD))
    dest = tmp_path / "FakePlant"
    dest.mkdir()
    res = core.seed(m, dest, {"RBB01": ["020R01"]})
    assert res["created"] == 1
    assert (dest / "RBB01" / "RB020R01B01").is_dir()
    assert not (dest / "RBB01" / "RB010R01B01").exists()
    assert not (dest / "RCB02").exists()


def test_seed_reports_progress_per_line(tmp_path):
    m = core.parse_source(_write_list(tmp_path, GOOD))
    dest = tmp_path / "FakePlant"
    dest.mkdir()
    ticks = []
    core.seed(m, dest, _selection(m), progress=lambda d, t, ln: ticks.append((d, t, ln)))
    assert ticks == [(0, 2, ""), (1, 2, "RBB01"), (2, 2, "RCB02")]


def test_seed_error_on_one_line_leaves_the_rest_intact(tmp_path):
    m = core.parse_source(_write_list(tmp_path, GOOD))
    dest = tmp_path / "FakePlant"
    dest.mkdir()
    (dest / "RBB01").write_text("a file where the line folder should be", encoding="utf-8")
    res = core.seed(m, dest, _selection(m))
    assert res["created"] == 1 and len(res["errors"]) == 2
    assert (dest / "RCB02" / "RC005R01B02" / "robot.json").is_file()


# ---- destination sanity ----------------------------------------------------------

def test_dest_warnings_flag_scanner_invisible_names(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "configured_library_root", lambda: "")
    assert core.dest_warnings(tmp_path / "2026_07_16")
    assert core.dest_warnings(tmp_path / "26_07_16")
    assert core.dest_warnings(tmp_path / "Latest")
    assert core.dest_warnings(tmp_path / "Plant.__part")
    assert core.dest_warnings(tmp_path / "LakeFake") == []


def test_dest_warnings_against_the_configured_root(tmp_path, monkeypatch):
    root = tmp_path / "RobotBackups"
    (root / "FakePlant" / "RBB01").mkdir(parents=True)
    monkeypatch.setattr(core, "configured_library_root", lambda: str(root))
    assert any("library folder itself" in w for w in core.dest_warnings(root))
    assert core.dest_warnings(root / "FakePlant") == []
    assert any("nested deeper" in w for w in core.dest_warnings(root / "FakePlant" / "RBB01"))
    assert any("won't show up" in w for w in core.dest_warnings(tmp_path / "Elsewhere"))
