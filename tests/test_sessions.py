"""The multi-session registry behind the browser-style backup tabs: open
several backups at once, each entry owning its own compare session; explicit
sids route reads to the right session regardless of which tab is active.
Fully synthetic: tmp_path backup folders + fake windows, no GUI."""
import inspect

from backupviewer.api import Api


def ok(res):
    assert res.get("ok"), res
    return res.get("data")


def err(res):
    assert not res.get("ok"), res
    return res["error"]["code"]


def mk_backup(tmp, name):
    d = tmp / name
    d.mkdir()
    (d / "SUMMARY.DG").write_text("Robot: %s\n" % name, encoding="utf-8")
    return d


class _Win:
    def __init__(self):
        self.js = []
        self.destroyed = False
        self.restored = False
        self.shown = False

    def evaluate_js(self, code):
        self.js.append(code)

    def restore(self):
        self.restored = True

    def show(self):
        self.shown = True

    def destroy(self):
        self.destroyed = True


def test_two_backups_open_independently(tmp_path):
    api = Api()
    a, b = mk_backup(tmp_path, "RB010R01B01"), mk_backup(tmp_path, "RB020R01B01")
    ma = ok(api.open_backup(str(a)))
    mb = ok(api.open_backup(str(b)))
    assert ma["sid"] != mb["sid"]
    assert len(api._sessions) == 2
    assert api._active_sid == mb["sid"]           # last open is active
    listed = ok(api.list_open_sessions())
    assert {x["sid"] for x in listed} == {ma["sid"], mb["sid"]}
    assert all(x["owner"] == "tab" for x in listed)


def test_switch_session_flips_active_and_returns_pair(tmp_path):
    api = Api()
    a, b = mk_backup(tmp_path, "RB010R01B01"), mk_backup(tmp_path, "RB020R01B01")
    ma = ok(api.open_backup(str(a)))
    ok(api.open_backup(str(b)))
    got = ok(api.switch_session(ma["sid"]))
    assert got["owner"] == "tab"
    assert got["manifest"]["sid"] == ma["sid"]
    assert got["compare"] is None
    assert ok(api.get_state())["sid"] == ma["sid"]


def test_explicit_sid_reads_the_right_session_while_another_is_active(tmp_path):
    api = Api()
    a, b = mk_backup(tmp_path, "RB010R01B01"), mk_backup(tmp_path, "RB020R01B01")
    ma = ok(api.open_backup(str(a)))
    mb = ok(api.open_backup(str(b)))
    # active is b, but sid-addressed access resolves a
    assert str(api._need_session(ma["sid"]).root) == ma["sid"]
    assert ok(api.get_state(ma["sid"]))["sid"] == ma["sid"]
    assert ok(api.get_state())["sid"] == mb["sid"]


def test_compare_is_per_entry(tmp_path):
    api = Api()
    a = mk_backup(tmp_path, "RB010R01B01")
    ca = mk_backup(tmp_path, "RB010R01B01_old")
    b = mk_backup(tmp_path, "RB020R01B01")
    ma = ok(api.open_backup(str(a)))
    ok(api.open_compare(str(ca)))
    mb = ok(api.open_backup(str(b)))            # a NEW entry - no compare of its own
    assert err(api.get_frames(mb["sid"], "b")) == "NO_COMPARE"
    # a's pairing survives b being opened and active
    assert str(api._side_session("b", ma["sid"]).root) == str(ca)
    ok(api.switch_session(ma["sid"]))
    assert str(api._side_session("b").root) == str(ca)


def test_close_session_evicts_and_fixes_active(tmp_path):
    api = Api()
    a, b = mk_backup(tmp_path, "RB010R01B01"), mk_backup(tmp_path, "RB020R01B01")
    ma = ok(api.open_backup(str(a)))
    mb = ok(api.open_backup(str(b)))
    ok(api.close_session(mb["sid"]))
    assert mb["sid"] not in api._sessions
    assert api._active_sid == ma["sid"]
    assert err(api.close_session(mb["sid"])) == "NO_BACKUP"   # unknown sid is loud


def test_session_cap_refuses_honestly(tmp_path):
    api = Api()
    for i in range(Api.MAX_OPEN_SESSIONS):
        ok(api.open_backup(str(mk_backup(tmp_path, "RB%03dR01B01" % i))))
    over = mk_backup(tmp_path, "RB999R01B01")
    assert err(api.open_backup(str(over))) == "SESSION_CAP"
    # re-opening an EXISTING sid is a replace, never a cap hit
    first = sorted(api._sessions)[0]
    ok(api.open_backup(first))
    assert len(api._sessions) == Api.MAX_OPEN_SESSIONS


def test_reopen_same_sid_replaces_in_place(tmp_path):
    api = Api()
    a = mk_backup(tmp_path, "RB010R01B01")
    ma = ok(api.open_backup(str(a)))
    old = api._need_session(ma["sid"])
    ok(api.open_compare(str(mk_backup(tmp_path, "RB010R01B01_old"))))
    ma2 = ok(api.open_backup(str(a)))
    assert ma2["sid"] == ma["sid"]
    assert len(api._sessions) == 1
    assert api._need_session(ma["sid"]) is not old      # fresh caches
    assert api._sessions[ma["sid"]]["compare"] is None  # refresh drops the pairing


def test_release_under_drops_entries_and_notifies(tmp_path):
    api = Api()
    api._window = _Win()
    a, b = mk_backup(tmp_path, "RB010R01B01"), mk_backup(tmp_path, "RB020R01B01")
    cb = mk_backup(tmp_path, "RB020R01B01_old")
    ma = ok(api.open_backup(str(a)))
    mb = ok(api.open_backup(str(b)))
    ok(api.switch_session(mb["sid"]))
    ok(api.open_compare(str(cb)))
    # a's whole folder moves -> its entry drops; cb moves -> b keeps its entry
    # but loses the compare
    api._release_sessions_under(str(a), str(cb))
    assert ma["sid"] not in api._sessions
    assert mb["sid"] in api._sessions
    assert api._sessions[mb["sid"]]["compare"] is None
    assert any("sessions-released" in code for code in api._window.js)


def test_popout_entry_drop_destroys_its_window(tmp_path):
    api = Api()
    a = mk_backup(tmp_path, "RB010R01B01")
    ma = ok(api.open_backup(str(a)))
    w = _Win()
    api._sessions[ma["sid"]]["owner"] = "popout"
    api._sessions[ma["sid"]]["window"] = w
    api._drop_session(ma["sid"])
    assert w.destroyed
    assert not api._sessions


def test_focus_main_workspace_raises_the_main_window_and_routes_it():
    """A pop-out has no workspace of its own: ctrl+e there brings you to the
    MAIN window's, rather than opening a second one over the same drafts."""
    api = Api()
    w = _Win()
    api._window = w
    assert ok(api.focus_main_workspace()) is True
    assert w.restored and w.shown
    assert any("openWorkspace" in code for code in w.js)


def test_focus_main_workspace_without_a_main_window_is_honest():
    api = Api()          # nothing bound (a headless run)
    assert err(api.focus_main_workspace()) == "NO_MAIN_WINDOW"


def test_sid_sits_immediately_before_side_everywhere():
    """Guards the JS SID_POS table: CLAUDE.md pins `side` trailing-positional,
    so `sid` must be the parameter directly before it on every side endpoint."""
    side_endpoints = [
        "get_frames", "get_io", "get_registers", "get_programs",
        "get_program_variables", "get_macros", "get_dcs_files", "get_dcs",
        "get_dcs_zones", "get_robot_pose", "get_sysvar_records", "get_sysvar",
        "get_mhvalves", "get_magnet", "get_payloads", "search_backup",
    ]
    for name in side_endpoints:
        params = list(inspect.signature(getattr(Api, name)).parameters)
        assert params[-1] == "side", (name, params)
        assert params[-2] == "sid", (name, params)
    # readers without side carry sid LAST
    for name in ["get_overview", "get_styles", "get_program",
                 "get_call_tree", "get_alarm_files", "get_alarms", "list_files",
                 "get_file", "get_photos", "get_image", "get_state"]:
        params = list(inspect.signature(getattr(Api, name)).parameters)
        assert params[-1] == "sid", (name, params)
