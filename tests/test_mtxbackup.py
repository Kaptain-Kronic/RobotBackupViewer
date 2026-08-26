"""Matrox camera SMB backup - exercised against a local temp 'camera home' via an
injected mount. In production the SMB share is just a UNC filesystem path once
authenticated, so a local dir is a faithful stand-in and the whole
enumerate -> copy -> library chain is verified with NO real camera. Live testing
is never the first validation."""
import datetime as _dt
import ftplib
import json
import os
from pathlib import Path

from backupviewer import ftpbackup, mtxbackup, library, settings
from backupviewer.session import BackupSession

# a minimal but real-shaped camera home. da/ is the config tree; SavedImages holds
# three dated folders of inspection triples, newest first below. Dotfiles, other
# home dirs, and the project's own HMIImage.jpg (which sits OUTSIDE any date
# folder) must be ignored entirely.
SI = "Documents/Matrox Design Assistant/SavedImages"
CAM = "CELL-01RB172-R01CAM02"
SHOT_A = SI + "/2026-07-07/" + CAM + "-401-0-Fail-2026_07_07-13.05.02.100"
SHOT_B = SI + "/2026-07-07/" + CAM + "-401-0-Pass-2026_07_07-9.14.30.020"
SHOT_C = SI + "/2026-06-25/" + CAM + "-401-0-Pass-2026_06_25-16.02.11.003"
SHOT_D = SI + "/2026-06-25/" + CAM + "-401-0-Fail-2026_06_25-8.30.00.500"
SHOT_E = SI + "/2026-05-02/" + CAM + "-401-0-Pass-2026_05_02-11.00.00.000"
# A is the newest photo on the camera even though B sorts after it as plain text -
# the camera writes hours before 10:00 with no leading zero.


def _sidecar(result: str, name: str = CAM) -> str:
    return ("Camera\nCamera Name: " + name + "\nCamera Type: Matrox GTX2000\n"
            "IP Address: 10.0.0.7\n\nInspection\nOverall Pass or Fail: " + result + "\n")


HOME = {
    ".bashrc": "export PS1='cam'\n",
    "autost.sh": "#!/bin/sh\n",
    "Downloads/junk.bin": "should not be pulled",
    "da/AgentSettings/agent.xml": "<agent/>\n",
    "da/Projects/SAMPLEPROJ/Settings/SAMPLEPROJ": "recipe blob\n",
    "da/Projects/SAMPLEPROJ/Persistent/uuid-1234": "persist\n",
    "da/DCFs/cam.dcf": "dcf\n",
    SI + "/HMIImage.jpg": "the project's own web tile - not an inspection",
    SHOT_A + ".jpg": "A jpg", SHOT_A + ".png": "A png bigger",
    SHOT_A + ".txt": _sidecar("Fail"),
    SHOT_B + ".jpg": "B jpg", SHOT_B + ".png": "B png bigger",
    # shot in the morning, before the camera was given its cell name: identity
    # comes from the NEWEST sidecar, so this factory hostname must lose
    SHOT_B + ".txt": _sidecar("Pass", "gtx000000"),
    SHOT_C + ".jpg": "C jpg", SHOT_C + ".png": "C png bigger",
    SHOT_C + ".txt": _sidecar("Pass"),
    SHOT_D + ".png": "D png only - the camera wrote no jpg",
    SHOT_D + ".txt": _sidecar("Fail"),
    SI + "/2026-06-25/orphan.txt": "a sidecar whose images are gone\n",
    SHOT_E + ".jpg": "E jpg", SHOT_E + ".png": "E png bigger",
    SHOT_E + ".txt": _sidecar("Pass"),
}

# the default 25-photo scope over that home: all of da/, the newest day's photos
# whole, older photos as image + sidecar with the png left behind (D has no jpg,
# so its png IS the image).
PULLED = [
    "da/AgentSettings/agent.xml",
    "da/Projects/SAMPLEPROJ/Settings/SAMPLEPROJ",
    "da/Projects/SAMPLEPROJ/Persistent/uuid-1234",
    "da/DCFs/cam.dcf",
    SHOT_A + ".jpg", SHOT_A + ".png", SHOT_A + ".txt",
    SHOT_B + ".jpg", SHOT_B + ".png", SHOT_B + ".txt",
    SHOT_C + ".jpg", SHOT_C + ".txt",
    SHOT_D + ".png", SHOT_D + ".txt",
    SHOT_E + ".jpg", SHOT_E + ".txt",
]
IGNORED = [
    ".bashrc", "autost.sh", "Downloads/junk.bin",
    SI + "/HMIImage.jpg",                      # not inside a date folder
    SI + "/2026-06-25/orphan.txt",             # a sidecar with no image is no photo
    SHOT_C + ".png", SHOT_E + ".png",          # older days keep the jpg, drop the png
]


def _make_camera(tmp_path, extra: dict | None = None) -> Path:
    home = tmp_path / "cam_home"
    for rel, body in {**HOME, **(extra or {})}.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return home


def _mount_factory(home):
    """A mount() stand-in: pretend to authenticate the SMB share and hand back the
    local home dir (+ a no-op cleanup). Ignores host so multi-camera tests reuse it."""
    def mount(host, user, passwd):
        return home, lambda: None
    return mount


def _has(base: Path, rel: str) -> bool:
    r"""isfile() through the \\?\ prefix. A dated camera snapshot inside pytest's
    own tmp tree runs past the 260-char MAX_PATH, where a plain Path.is_file()
    answers False for a file that is really sitting right there - the app reads
    and writes these trees the same way (ftpbackup.long_path)."""
    return os.path.isfile(ftpbackup.long_path(base.joinpath(*rel.split("/"))))


def _iso_lib(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdata)


def test_probe_camera(tmp_path):
    home = _make_camera(tmp_path)
    res = mtxbackup.probe_camera("10.0.0.7", mount=_mount_factory(home))
    assert res["reachable"] is True
    assert res["has_da"] is True
    assert res["has_images"] is True
    assert res["error"] == ""


def test_probe_camera_unreachable():
    def dead_mount(host, user, passwd):
        raise OSError("SMB connect failed (WinError 53)")
    res = mtxbackup.probe_camera("10.0.0.9", mount=dead_mount)
    assert res["reachable"] is False
    assert res["error"]


def test_diagnose_camera(tmp_path):
    home = _make_camera(tmp_path)
    res = mtxbackup.diagnose_camera("10.0.0.7", mount=_mount_factory(home))
    assert res["newest_date"] == "2026-07-07"
    assert "2026-06-25" in res["image_dates"] and "2026-07-07" in res["image_dates"]
    assert "da" in res["home_list"]
    assert res["error"] == ""


def test_resolve_camera_name(tmp_path):
    home = _make_camera(tmp_path)
    ident = mtxbackup.resolve_camera_name("10.0.0.7", mount=_mount_factory(home))
    assert ident["name"] == "CELL-01RB172-R01CAM02"     # from the newest sidecar
    assert ident["model"] == "Matrox GTX2000"


def test_enumerate_scope(tmp_path):
    """Enumerate returns all of da/ + the newest N photos: whole triples for the
    newest day, image+sidecar for older ones, and nothing that is not a photo."""
    home = _make_camera(tmp_path)
    rels = {p.relative_to(home).as_posix() for p in mtxbackup._enumerate_files(home)}
    assert rels == set(PULLED), sorted(rels)


def _photo_rels(home, limit):
    si = home.joinpath(*mtxbackup.IMAGES_PARTS)
    return [p.relative_to(home).as_posix() for p in mtxbackup._photo_files(si, limit)]


def test_photo_limit_counts_photos_not_days(tmp_path):
    """The window is N PHOTOS, newest first, and it walks back through date
    folders to fill - one photo means one photo, not one day of them."""
    home = _make_camera(tmp_path)
    assert _photo_rels(home, 0) == []
    # A, not B: the camera writes 9am with no leading zero, and a plain text
    # sort puts "9.14" after "13.05" - the morning shot is NOT the newest
    assert _photo_rels(home, 1) == [SHOT_A + ".jpg", SHOT_A + ".png", SHOT_A + ".txt"]
    # the third photo lives a fortnight back, and crossing the day boundary is
    # what drops the png
    assert _photo_rels(home, 3) == [
        SHOT_A + ".jpg", SHOT_A + ".png", SHOT_A + ".txt",
        SHOT_B + ".jpg", SHOT_B + ".png", SHOT_B + ".txt",
        SHOT_C + ".jpg", SHOT_C + ".txt",
    ]
    # every photo the home holds - and a photo with no jpg keeps its png rather
    # than going missing from the snapshot
    assert set(_photo_rels(home, 25)) == {r for r in PULLED if not r.startswith("da/")}


def test_photo_limit_bounds_one_busy_day(tmp_path):
    """A single day with more inspections than the window still yields exactly
    the window - the newest ones."""
    home = _make_camera(tmp_path)
    day = SI + "/2026-07-07/" + CAM + "-401-0-Pass-2026_07_07-2%d.00.00.000"
    for i in range(4):
        for ext in (".jpg", ".png", ".txt"):
            p = home / ((day % i) + ext)
            p.write_text("extra", encoding="utf-8")
    rels = _photo_rels(home, 2)
    assert len(rels) == 6                                   # two whole triples
    assert all("2026-07-07" in r for r in rels)
    assert (day % 3) + ".jpg" in rels and (day % 2) + ".jpg" in rels   # the newest two


def test_copy_over_max_path(tmp_path):
    r"""A destination past the 260-char Windows MAX_PATH still copies (the
    halfway-through 'cannot find the path' field failure) via the \\?\ prefix."""
    import os
    from backupviewer import ftpbackup
    src = tmp_path / "src.png"
    src.write_bytes(b"x" * 512)
    deep = tmp_path
    for i in range(6):
        deep = deep / ("Matrox Design Assistant SavedImages segment %02d" % i)
    dest = deep / "CELL-01RB172-R01CAM02-402-0-Fail-2026_07_16-verylongfilename.png"
    assert len(str(dest)) > 260, len(str(dest))
    n = mtxbackup._copy_file(src, dest)
    assert n == 512
    assert os.path.exists(ftpbackup.long_path(dest))
    assert not os.path.exists(ftpbackup.long_path(dest.with_name(dest.name + ".part")))


def test_session_reads_over_max_path(tmp_path):
    r"""The session walk survives MAX_PATH too. The writer lands SavedImages
    past 260 chars via \\?\ (test above), but with the OS long-path policy off
    a plain is_file() on such a path is a failed stat = False, so the index
    silently dropped every photo - "no photos" with the photos right there on
    disk. The session must index, rel() and read them, without the \\?\ walk
    root leaking into the manifest path."""
    src = tmp_path / "seed.jpg"
    src.write_bytes(b"\xff\xd8seed")
    root = tmp_path / "cell"
    day = root / "CAM1" / "Documents" / "Matrox Design Assistant" / "SavedImages" / "2026-07-07"
    name = "CELL-01RB172-R01CAM02-402-0-Pass-2026_07_07-" + "0" * 200
    for ext in (".jpg", ".txt"):
        dest = day / (name + ext)
        assert len(str(dest)) > 260, len(str(dest))
        mtxbackup._copy_file(src, dest)

    s = BackupSession(root)
    assert s.has_photos()
    rel = "CAM1/Documents/Matrox Design Assistant/SavedImages/2026-07-07/" + name + ".jpg"
    p = s.files.get(rel.upper())
    assert p is not None, sorted(s.files)
    assert p.read_bytes().startswith(b"\xff\xd8")
    assert s.rel(p) == rel
    m = s.manifest()
    assert m["file_count"] == 2
    assert m["tabs"]["photos"] is True
    assert m["path"] == str(root)


def test_backup_end_to_end(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    home = _make_camera(tmp_path)
    dest = tmp_path / "MTXBackups"

    registered = {}

    def on_complete(job):
        registered["entry"] = library.register_backup(
            job.library_match(), job.library_backup(),
            latest_path=job.snapshot().get("latest_path", ""),
        )

    job = mtxbackup.CameraBackupJob(
        "10.0.0.7", dest, "FAKEPLANT", "RBB01", "RB172R01",
        note="post-PM camera pull", mount=_mount_factory(home), throttle=0,
        on_complete=on_complete,
    )
    res = job.run()

    assert res["status"] == "done", res
    assert res["done"] == len(PULLED)
    assert res["bytes"] > 0
    assert res["device_type"] == "camera-mtx"

    dated = Path(res["dated_path"])
    assert dated.is_dir()
    assert "RBB01" in dated.parts and "RB172R01" in dated.parts
    cam = dated / "CAM1"
    for rel in PULLED:
        assert _has(cam, rel), rel
    for rel in IGNORED:
        assert not _has(cam, rel), rel
    assert not list(dated.rglob("*.part"))
    assert (dated / "notes.txt").read_text(encoding="utf-8").startswith("post-PM camera pull")
    md = json.loads((dated / "backup.json").read_text(encoding="utf-8"))
    assert md["type"] == mtxbackup.BACKUP_TYPE and md["device_type"] == "camera-mtx"
    assert md["source"] == "smb"
    assert md["complete"] is True                 # flipped true only on success

    latest = Path(res["latest_path"])
    assert latest.is_dir()
    assert latest.parts[-2:] == ("Latest", "RB172R01")
    assert latest.joinpath("CAM1", "da", "DCFs", "cam.dcf").is_file()

    data = library.list_robots()
    assert len(data["robots"]) == 1
    e = data["robots"][0]
    assert e["robot"] == "RB172R01" and e["line"] == "RBB01" and e["plant"] == "FAKEPLANT"
    assert e.get("device_type") == "camera-mtx"
    assert "10.0.0.7" in e.get("ips", [])
    assert len(e["backups"]) == 1
    assert registered["entry"]["id"] == e["id"]

    m = BackupSession(latest).manifest()
    assert m["file_count"] >= len(PULLED)


# -- topping up instead of stacking a near-twin ----------------------------------

class _Clock:
    """Stands in for the backup engine's clock. Two pulls inside one test have to
    land in different <HH_MM_SS> folders; a real run takes milliseconds."""

    def __init__(self, when):
        self.t = when

    def __call__(self):
        return self.t


NEW_SHOT = SI + "/2026-07-08/" + CAM + "-401-0-Fail-2026_07_08-7.05.00.001"


def _shoot(home: Path, stem: str, result: str = "Fail") -> None:
    """The camera runs another part and saves the triple for it."""
    for ext, body in ((".jpg", "jpg"), (".png", "png bigger"), (".txt", _sidecar(result))):
        p = home / (stem + ext)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def _camera_job(home, dest, store=None, **kw):
    def on_complete(job):
        store["entry"] = library.register_backup(
            job.library_match(), job.library_backup(),
            latest_path=job.snapshot().get("latest_path", ""),
        )
    return mtxbackup.CameraBackupJob(
        "10.0.0.7", dest, "FAKEPLANT", "RBB01", "RB172R01",
        mount=_mount_factory(home), throttle=0,
        on_complete=(on_complete if store is not None else None), **kw)


def _meta(snap: Path) -> dict:
    return json.loads((snap / "backup.json").read_text(encoding="utf-8"))


def test_topup_folds_new_photos_into_the_matching_snapshot(monkeypatch, tmp_path):
    """A re-run whose da/ tree is unchanged adds its new photos to the snapshot
    they match instead of stacking a 360-file near-twin beside it. The snapshot
    keeps its own `taken`, records the visit, recounts itself off disk, and the
    library keeps ONE row for the one folder."""
    _iso_lib(monkeypatch, tmp_path)
    clock = _Clock(_dt.datetime(2026, 7, 7, 14, 0, 0))
    monkeypatch.setattr(ftpbackup, "_now", clock)
    home, dest, store = _make_camera(tmp_path), tmp_path / "MTXBackups", {}

    first = _camera_job(home, dest, store).run()
    kept = Path(first["dated_path"])
    taken = _meta(kept)["taken"]

    _shoot(home, NEW_SHOT)                       # the camera inspects another part
    clock.t = _dt.datetime(2026, 7, 8, 7, 30, 0)
    second = _camera_job(home, dest, store).run()

    assert second["status"] == "done", second
    assert Path(second["dated_path"]) == kept                  # folded in, not stacked
    assert second["photos_added"] == 3
    robot = kept.parent.parent
    assert [d.name for d in robot.iterdir() if d.is_dir()] == [kept.parent.name]

    cam = kept / "CAM1"
    for ext in (".jpg", ".png", ".txt"):
        assert _has(cam, NEW_SHOT + ext), ext
    assert _has(cam, SHOT_A + ".png")            # a fold only ADDS: what was already
    assert _has(cam, "da/DCFs/cam.dcf")          # in the snapshot is still there

    md = _meta(kept)
    assert md["taken"] == taken                  # the snapshot keeps its own moment
    assert md["updated"] == "2026-07-08T07:30:00"
    assert md["topups"] == 1
    assert md["complete"] is True
    assert md["files"] == len(PULLED) + 3
    assert md["files"] == sum(1 for _rel, _f in mtxbackup._walk_rels(kept))

    e = library.list_robots()["robots"][0]
    assert len(e["backups"]) == 1                              # one folder, one row
    assert e["backups"][0]["path"] == str(kept)
    assert e["backups"][0]["files"] == md["files"]
    assert e["last_backup"] == md["updated"]     # visited today, not stale since the 7th
    assert _has(Path(second["latest_path"]) / "CAM1", NEW_SHOT + ".jpg")


def test_topup_skipped_when_the_da_tree_changed(monkeypatch, tmp_path):
    """A project edit is a real change and earns its own snapshot - the
    photos-only shortcut must never swallow one."""
    _iso_lib(monkeypatch, tmp_path)
    clock = _Clock(_dt.datetime(2026, 7, 7, 14, 0, 0))
    monkeypatch.setattr(ftpbackup, "_now", clock)
    home, dest, store = _make_camera(tmp_path), tmp_path / "MTXBackups", {}

    first = _camera_job(home, dest, store).run()
    kept = Path(first["dated_path"])

    (home / "da" / "DCFs" / "cam.dcf").write_text("dcf v2 - the project changed\n",
                                                  encoding="utf-8")
    _shoot(home, NEW_SHOT)
    clock.t = _dt.datetime(2026, 7, 8, 7, 30, 0)
    second = _camera_job(home, dest, store).run()

    assert Path(second["dated_path"]) != kept
    assert Path(second["dated_path"]).is_dir() and kept.is_dir()
    assert "updated" not in _meta(kept) and "topups" not in _meta(kept)
    assert len(library.list_robots()["robots"][0]["backups"]) == 2


def test_topup_never_tops_up_a_partial_snapshot(monkeypatch, tmp_path):
    """A snapshot whose pull died mid-download is never topped up: adding today's
    photos to a tree that is missing files would dress a half-backup as a fuller
    one, and its marker is the only thing that says so."""
    _iso_lib(monkeypatch, tmp_path)
    clock = _Clock(_dt.datetime(2026, 7, 7, 14, 0, 0))
    monkeypatch.setattr(ftpbackup, "_now", clock)
    home, dest = _make_camera(tmp_path), tmp_path / "MTXBackups"

    partial = Path(_camera_job(home, dest).run()["dated_path"])
    md = _meta(partial)
    md["complete"] = False
    (partial / "backup.json").write_text(json.dumps(md), encoding="utf-8")

    _shoot(home, NEW_SHOT)
    clock.t = _dt.datetime(2026, 7, 8, 7, 30, 0)
    second = _camera_job(home, dest).run()

    assert Path(second["dated_path"]) != partial
    assert _meta(partial)["complete"] is False                 # left exactly as found
    assert "updated" not in _meta(partial)


def test_partial_backup_left_marked_incomplete(tmp_path):
    """A camera pull that lands no files leaves its dated folder marked
    complete:false, so the library rescan never adopts the partial as latest -
    the same started-marker guarantee the FANUC job makes (ftpbackup)."""
    empty = tmp_path / "empty_share"
    empty.mkdir()
    job = mtxbackup.CameraBackupJob("10.0.0.9", tmp_path / "lib", "P", "L", "STATION",
                                    mount=_mount_factory(empty), throttle=0)
    res = job.run()
    assert res["status"] == "error"
    dated = Path(res["dated_path"])
    md = json.loads((dated / "backup.json").read_text(encoding="utf-8"))
    assert md["complete"] is False
    assert not (dated / "notes.txt").exists()     # success sidecars never ran


def test_run_id_in_snapshot(tmp_path):
    """A camera job carries its run_id in the snapshot so an in-flight pull holds
    a run open for join/retry-fold (api._active_run_id)."""
    job = mtxbackup.CameraBackupJob("10.0.0.7", tmp_path / "o", "P", "L", "R",
                                    run_id="run-cam", mount=_mount_factory(tmp_path))
    assert job.snapshot()["run_id"] == "run-cam"


def test_multi_camera_layout(monkeypatch, tmp_path):
    """Two cameras on one station land in CAM1/ and CAM2/ of one snapshot."""
    _iso_lib(monkeypatch, tmp_path)
    home = _make_camera(tmp_path)
    dest = tmp_path / "MTXBackups"
    job = mtxbackup.CameraBackupJob(
        "10.0.0.7", dest, "FAKEPLANT", "RBB01", "RB172R01",
        cameras=[{"label": "CAM1", "host": "10.0.0.7"},
                 {"label": "CAM2", "host": "10.0.0.8"}],
        mount=_mount_factory(home), throttle=0,
    )
    res = job.run()
    assert res["status"] == "done", res
    assert res["done"] == 2 * len(PULLED)
    dated = Path(res["dated_path"])
    assert dated.joinpath("CAM1", "da", "DCFs", "cam.dcf").is_file()
    assert dated.joinpath("CAM2", "da", "DCFs", "cam.dcf").is_file()


def test_backup_cancel(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    home = _make_camera(tmp_path)
    job = mtxbackup.CameraBackupJob("10.0.0.7", tmp_path / "out", "P", "L", "R",
                                    mount=_mount_factory(home), throttle=0)
    job.cancel()
    res = job.run()
    assert res["status"] == "cancelled"
    assert library.list_robots()["robots"] == []  # nothing registered on cancel


def test_name_from_backup(monkeypatch, tmp_path):
    """A pulled snapshot teaches the camera's real name offline (newest sidecar)."""
    _iso_lib(monkeypatch, tmp_path)
    home = _make_camera(tmp_path)
    job = mtxbackup.CameraBackupJob("10.0.0.7", tmp_path / "out", "P", "L", "CAM",
                                    mount=_mount_factory(home), throttle=0)
    res = job.run()
    assert res["status"] == "done", res
    ident = mtxbackup.name_from_backup(res["dated_path"])
    assert ident == {"name": "CELL-01RB172-R01CAM02", "model": "Matrox GTX2000"}


def test_name_from_backup_blank_on_nothing(tmp_path):
    """No sidecars / no folder -> blanks, never an exception."""
    (tmp_path / "CAM1" / "da").mkdir(parents=True)
    assert mtxbackup.name_from_backup(tmp_path) == {"name": "", "model": ""}
    assert mtxbackup.name_from_backup("") == {"name": "", "model": ""}
    assert mtxbackup.name_from_backup(tmp_path / "nope") == {"name": "", "model": ""}


def test_camera_self_names_and_links_after_first_backup(monkeypatch, tmp_path):
    """The api register flow end-to-end: a placeholder(IP)-named camera renames
    itself from the snapshot it just pulled (old name kept as an alias, model
    filled) and auto-links to its robot - the user story 'first backup teaches
    the camera who it is'."""
    _iso_lib(monkeypatch, tmp_path)
    # the dest root IS the library root (api._start_backup_job persists it as
    # such before every job starts) - teach renames the camera folder under it
    lib_root = tmp_path / "MTXBackups"
    monkeypatch.setattr(settings, "library_root", lambda: str(lib_root))
    home = _make_camera(tmp_path)
    robot = library.add_robot({"robot": "RB172R01B01", "plant": "FAKEPLANT",
                               "line": "RBB01", "device_type": "robot"})
    cam = library.add_robot({"robot": "10.0.0.7", "plant": "FAKEPLANT",
                             "line": "RBB01", "device_type": "camera-mtx",
                             "ips": ["10.0.0.7"]})

    def register(job):       # exactly what api._start_backup_job's on_complete does
        entry = library.register_backup(job.library_match(), job.library_backup(),
                                        latest_path=job.snapshot().get("latest_path", ""))
        ident = mtxbackup.name_from_backup(job.snapshot().get("dated_path", ""))
        if ident.get("name"):
            library.teach_camera_name(entry["id"], ident["name"], ident.get("model", ""))
        library.auto_link_cameras()

    job = mtxbackup.CameraBackupJob("10.0.0.7", lib_root, "FAKEPLANT",
                                    "RBB01", "10.0.0.7", mount=_mount_factory(home),
                                    throttle=0, on_complete=register)
    assert job.run()["status"] == "done"

    e = next(x for x in library.list_robots()["robots"] if x["id"] == cam["id"])
    assert e["robot"] == "CELL-01RB172-R01CAM02"            # self-named
    assert e["model"] == "Matrox GTX2000"
    assert {"plant": "FAKEPLANT", "line": "RBB01", "robot": "10.0.0.7"} in e["aliases"]
    assert e["linked_robot_id"] == robot["id"]             # and auto-linked

    # the teach renamed the camera's FOLDER with it - so the first library
    # rescan (a finished backup always triggers one) re-derives the same name
    # instead of reverting it to the IP. The revert was the original bug: the
    # name held only until the next scan, which believed the folder.
    assert (lib_root / "FAKEPLANT" / "RBB01" / "CELL-01RB172-R01CAM02").is_dir()
    library.scan_library_root(lib_root)
    e2 = next(x for x in library.list_robots()["robots"] if x["id"] == cam["id"])
    assert e2["robot"] == "CELL-01RB172-R01CAM02"          # STILL self-named
    assert e2["linked_robot_id"] == robot["id"]


# -- the FANUC guard still refuses an FTP host that looks like a camera ----------

class _CameraFTP:
    """A tiny FTP stand-in whose root looks like a Matrox home (da/ + Documents/):
    a FANUC BackupJob pointed here must refuse rather than pull junk."""

    def __init__(self, timeout=None):
        self._cwd = "/"

    def connect(self, host, port=21):
        self.host = host

    def login(self, user="", passwd=""):
        pass

    def set_pasv(self, flag):
        pass

    def getwelcome(self):
        return "220 ready"

    def cwd(self, path):
        if path in ("/", ""):
            self._cwd = "/"
            return "250 ok"
        raise ftplib.error_perm("550 no device: " + path)   # no MD:, roots at /

    def nlst(self, *args):
        return ["da", "Documents", "autost.sh"]

    def quit(self):
        pass

    def close(self):
        pass


def test_fanuc_job_refuses_matrox_ftp_host(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    job = ftpbackup.BackupJob("10.0.0.7", tmp_path / "out", "P", "L", "CAM-AS-ROBOT",
                              ftp_factory=lambda timeout=None: _CameraFTP(timeout), throttle=0)
    res = job.run()
    assert res["status"] == "error", res
    assert "matrox camera" in res["error"].lower()
    assert library.list_robots()["robots"] == []
