"""Fleet health scan tests - fully synthetic (no SampleBackup needed): a
FakeSession feeds the checks parser-validated file texts, and the job runs
over fake robots with an injected session factory + search fn."""
import os
from datetime import datetime

from backupviewer import healthscan
from backupviewer.healthscan import (
    HealthScanJob, _RobotData, _check_adv_dcs, _check_battery,
    _check_broken_calls, _check_cip, _check_clock, _check_mastering,
    _check_override, _check_payload, _check_remarked_logic,
    _check_remarked_positions, _check_sigs, _check_style_broken,
    _check_style_orphans, _check_sw_version, _check_uninit_points,
    _check_uninit_prs, _parse_tolerance, norm_queries,
)

# -- fixture texts (formats validated against the real parsers) -------------------

DCSVRFY_BAD_SIG = """DATE: 15-MAY-26 09:54
DCS Version: V3.5.21
F Number: F999999

--- Signature number ---------------
 1 TP and KAREL program:   976310544   976310544
    Time: 15-MAY-26 09:54    15-MAY-26 09:54
 2 DCS Parameter:   111222333   999888777
    Time: 15-MAY-26 09:54    01-JAN-26 08:00
--- CIP Safety ---------------------
 1 CIP Safety: DISABLE    OK
 2 Input size: 0    OK
--- Robot setup --------------------
 1 Process: DCS    OK
"""

DCSVRFY_CLEAN = """DATE: 15-MAY-26 09:54
DCS Version: V3.5.21
F Number: F111111

--- Signature number ---------------
 1 TP and KAREL program:   555000111   555000111
    Time: 15-MAY-26 09:54    15-MAY-26 09:54
--- CIP Safety ---------------------
 1 CIP Safety: ENABLE    OK
"""

SUMMARY_ADV = """<H2><A NAME="1">Version Information</A></H2>
<PRE>
 F Number: F999999
CONFIG::
 DCS Pos./Speed Check                J567
 Multi-Tasking                       J600
</PRE>
"""

SUMMARY_PLAIN = """<H2><A NAME="1">Version Information</A></H2>
<PRE>
 F Number: F111111
CONFIG::
 Multi-Tasking                       J600
</PRE>
"""

SYSMAST_G2_UNMASTERED = """[*SYSTEM*]$DMR_GRP  Storage: SHADOW  Access: RW  : ARRAY[2] OF DMR_GRP_T
     Field: $DMR_GRP[1].$MASTER_DONE Access: RW: BOOLEAN = TRUE
     Field: $DMR_GRP[1].$REF_DONE Access: RW: BOOLEAN = TRUE
     Field: $DMR_GRP[1].$MASTER_COUN  ARRAY[9] OF INTEGER
      [1] = -503786
      [2] = 1200450
     Field: $DMR_GRP[2].$MASTER_DONE Access: RW: BOOLEAN = FALSE
     Field: $DMR_GRP[2].$MASTER_COUN  ARRAY[9] OF INTEGER
      [1] = 0
"""

SYSMAST_OK = """[*SYSTEM*]$DMR_GRP  Storage: SHADOW  Access: RW  : ARRAY[1] OF DMR_GRP_T
     Field: $DMR_GRP[1].$MASTER_DONE Access: RW: BOOLEAN = TRUE
     Field: $DMR_GRP[1].$REF_DONE Access: RW: BOOLEAN = TRUE
     Field: $DMR_GRP[1].$MASTER_COUN  ARRAY[9] OF INTEGER
      [1] = -503786
      [2] = 1200450
"""

CELLIO = """[*CELLIO*]$STYLE_NAME  Storage: CMOS  Access: RW  : ARRAY[4] OF STRING[12]
    [1] = 'S04PICK1'
    [2] = 'S05GONE'
    [3] = '********'
[*CELLIO*]$STYLE_COMNT  Storage: CMOS  Access: RW  : ARRAY[4] OF STRING[16]
    [1] = 'PICK STYLE'
"""

# style 2's program is missing but the style is DISABLED - a placeholder, not a fault
CELLIO_PARKED = """[*CELLIO*]$STYLE_NAME  Storage: CMOS  Access: RW  : ARRAY[4] OF STRING[12]
    [1] = 'S04PICK1'
    [2] = 'MOVREPR2'
[*CELLIO*]$STYLE_ENAB  Storage: CMOS  Access: RW  : ARRAY[4] OF BOOLEAN
    [1] = TRUE
    [2] = FALSE
"""

# full identity block (edition + version + servo + DCS) - two editions for the
# line-drift pass. Field names verified against summary_dg.parse_summary.
SUMMARY_V833 = """<H2><A NAME="1">Version Information</A></H2>
<PRE>
 F Number: F999999
 $VERSION: V8.33 5/12/2023
VERSION INFORMATION::
SOFTWARE:
 S/W Serial No.  : F999999
 Servo Code      : V26.14
 DCS             : V3.5.21
 Software Edition No. : V8.33P/16
CONFIG::
 Multi-Tasking                       J600
</PRE>
"""

SUMMARY_V830 = SUMMARY_V833.replace("V8.33P/16", "V8.30P/12").replace("V8.33", "V8.30")

# quote-delimited alarm history rows (format verified against parse_alarm_file);
# row 1 is the battery alarm, the others make sure only real BLALs count
ERRALL_BLAL = """ERRALL.LS      Robot Name RB101R01B01 09-JUN-26 16:20:54
    32607" 09-JUN-26 16:17:38 " SRVO-065 BLAL alarm (Group:1 Axis:2)  " " SERVO   00110110" act"
    32606" 08-JUN-26 12:00:00 " SRVO-037 IMSTP input (Group:1)  " " SERVO   00110110""
    32605" 07-JUN-26 09:30:00 " R E S E T   " " RESET   00000000""
"""

ERRALL_QUIET = """ERRALL.LS      Robot Name RB101R01B01 09-JUN-26 16:20:54
    32606" 08-JUN-26 12:00:00 " SRVO-037 IMSTP input (Group:1)  " " SERVO   00110110""
"""

# every schedule factory-fresh (mass 100 default / -9999 sentinel, no comment/CG)
SYMOTN_UNSET = """[*SYSTEM*]$PLST_GRP1  Storage: CMOS  Access: RW  : ARRAY[10] OF PLST_GRP_T
     Field: $PLST_GRP1[1].$COMMENT Access: RW: STRING[16] = ''
     Field: $PLST_GRP1[1].$PAYLOAD Access: RW: REAL = 100
     Field: $PLST_GRP1[1].$PAYLOAD_X Access: RW: REAL = 0
     Field: $PLST_GRP1[1].$PAYLOAD_Y Access: RW: REAL = 0
     Field: $PLST_GRP1[1].$PAYLOAD_Z Access: RW: REAL = 0
     Field: $PLST_GRP1[2].$COMMENT Access: RW: STRING[16] = ''
     Field: $PLST_GRP1[2].$PAYLOAD Access: RW: REAL = -9999
     Field: $PLST_GRP1[2].$PAYLOAD_X Access: RW: REAL = 0
"""

SYMOTN_SET = """[*SYSTEM*]$PLST_GRP1  Storage: CMOS  Access: RW  : ARRAY[10] OF PLST_GRP_T
     Field: $PLST_GRP1[1].$COMMENT Access: RW: STRING[16] = 'EOAT'
     Field: $PLST_GRP1[1].$PAYLOAD Access: RW: REAL = 55.3
     Field: $PLST_GRP1[1].$PAYLOAD_X Access: RW: REAL = 1.2
     Field: $PLST_GRP1[1].$PAYLOAD_Y Access: RW: REAL = 0
     Field: $PLST_GRP1[1].$PAYLOAD_Z Access: RW: REAL = 8.4
     Field: $PLST_GRP1[2].$COMMENT Access: RW: STRING[16] = ''
     Field: $PLST_GRP1[2].$PAYLOAD Access: RW: REAL = 100
     Field: $PLST_GRP1[2].$PAYLOAD_X Access: RW: REAL = 0
"""

# mass changed but no comment/CG - still "configured" (mass-only setups exist)
SYMOTN_MASS_ONLY = SYMOTN_SET.replace("'EOAT'", "''").replace("REAL = 1.2", "REAL = 0") \
                             .replace("REAL = 8.4", "REAL = 0")

# -- positions / remarks / config fixtures (shapes mirror the real listings) -------

# a taught P[1], an untaught P[2], a REMARKED ref (never counts), a comment
# mention (never counts), an indirect P[R[..]] (counted, not resolved), and a
# circular whose SECOND point rides an unnumbered continuation line
LS_POINTS = """/PROG TESTPTS
/ATTR
COMMENT\t\t= "POINTS";
/MN
   1:J P[1] 100% FINE ;
   2:L P[2] 500mm/sec CNT50 ;
   3:  //J P[3] 50% FINE ;
   4:  !move P[9] later ;
   5:L P[R[4]] 200mm/sec FINE ;
   6:C P[1]
    :  P[4] 300mm/sec FINE ;
/POS
P[1]{
   GP1:
    UF : 0, UT : 1,  CONFIG : 'N U T, 0, 0, 0',
    X = 100.0 mm, Y = 200.0 mm, Z = 300.0 mm,
    W = 0.0 deg, P = 10.0 deg, R = 0.0 deg
};
/END
"""

LS_POINTS_OK = """/PROG CLEANPTS
/MN
   1:J P[1] 100% FINE ;
/POS
P[1]{
   GP1:
    UF : 0, UT : 1,  CONFIG : 'N U T, 0, 0, 0',
    X = 1.0 mm, Y = 2.0 mm, Z = 3.0 mm,
    W = 0.0 deg, P = 0.0 deg, R = 0.0 deg
};
/END
"""

# remarked LOGIC only (CALL / assignment) - the motion check must stay quiet
LS_LOGIC = """/PROG LOGICP
/MN
   1:  //CALL GET_HOME ;
   2:  //UTOOL_NUM=1 ;
   3:J P[1] 100% FINE ;
/POS
P[1]{
   GP1:
    UF : 0, UT : 1,  CONFIG : 'N U T, 0, 0, 0',
    X = 1.0 mm, Y = 2.0 mm, Z = 3.0 mm,
    W = 0.0 deg, P = 0.0 deg, R = 0.0 deg
};
/END
"""

# PR reads: PR[1] initialized, PR[7] read as an offset, PR[2,1] element read;
# a comment + a remarked line that must NOT count, and an indirect PR[R[..]]
LS_PR = """/PROG PRMOVE
/MN
   1:J PR[1:Home] 100% FINE ;
   2:L P[1] 100mm/sec FINE Offset,PR[7] ;
   3:  !uses PR[2] someday ;
   4:  //J PR[2] 100% FINE ;
   5:R[3]=PR[2,1] ;
   6:J PR[R[10]] 100% FINE ;
/POS
P[1]{
   GP1:
    UF : 0, UT : 1,  CONFIG : 'N U T, 0, 0, 0',
    X = 1.0 mm, Y = 2.0 mm, Z = 3.0 mm,
    W = 0.0 deg, P = 0.0 deg, R = 0.0 deg
};
/END
"""

# writes both uninitialized PRs the reader touches (write targets never count
# as reads; their presence demotes the reader's flag to info)
LS_PR_INIT = """/PROG INITPR
/MN
   1:PR[2]=LPOS ;
   2:PR[7]=JPOS ;
/END
"""

# format mirrored from a real POSREG.VA: one taught entry, uninitialized rest
POSREG_MIX = """[*POSREG*]$POSREG  Storage: SHADOW  Access: RW  : ARRAY[1,200] OF Position Reg
    [1,1] =   'Home'   Group: 1
  J1 =     9.998 deg   J2 =   -45.000 deg   J3 =    30.000 deg
  J4 =    24.998 deg   J5 =   -90.000 deg   J6 =   -90.000 deg

    [1,2] =   'Home2' Uninitialized
    [1,7] =   'Spare' Uninitialized
"""

# the VA "Field:" line SYCLDINT.VA prints (plus a string-reference trap that
# must never match - $CONDET stores the NAME of the variable, not its value)
SYCLDINT_100 = """   Field: $MCR.$OT_RELEASE Access: RW: BOOLEAN = FALSE
   Field: $MCR.$GENOVERRIDE Access: RW: INTEGER = 100
   Field: $MCR.$FLTR_DEBUG Access: RW: INTEGER = 0
"""
SYCLDINT_55 = SYCLDINT_100.replace("INTEGER = 100", "INTEGER = 55")
SYCLDINT_TRAP = ("       Field: $CONDET_CFG.$EXT_DATA[5].$VAR_NAME Access: RW:"
                 " STRING[41] = '$MCR.$GENOVERRIDE'\n")

BACKDATE = "26/06/11 08:55:56\n\nVersion:  V8.33P/16/None\n"
SUMMARY_CLOCK = "F Number: F999999\nDATE:     11-JUN-26 08:56 \n\nSUMMARY::\n"


class FakeSession:
    def __init__(self, files: dict, program_files=(), alarm_files=(), karel=(),
                 paths=None):
        self._files = {k.upper(): v for k, v in files.items()}
        self.program_files = list(program_files)
        self._alarm_files = list(alarm_files)
        self.karel_programs = {k.upper(): {} for k in karel}
        self._paths = {k.upper(): v for k, v in (paths or {}).items()}
        self._cache: dict = {}

    def text(self, name):
        return self._files.get(name.upper())

    def find(self, name):
        # the real session returns a Path; tests that need one (clock mtime)
        # pass it via `paths` — everyone else keeps the truthy-presence shape
        return self._paths.get(name.upper()) or (name.upper() in self._files)

    def alarm_files(self):
        return self._alarm_files

    def cached(self, key, builder):
        if key not in self._cache:
            self._cache[key] = builder()
        return self._cache[key]


def _prog(tmp_path, stem, body_lines):
    p = tmp_path / (stem + ".LS")
    p.write_text("/PROG " + stem + "\n/MN\n" +
                 "".join(f"   {i + 1}:  {b} ;\n" for i, b in enumerate(body_lines)) +
                 "/POS\n/END\n", encoding="utf-8")
    return p


# -- registry ---------------------------------------------------------------------

def test_registry_and_valid_ids():
    ids = [c["id"] for c in healthscan.check_list()]
    assert ids == ["adv_dcs", "sig_mismatch", "cip_safety",
                   "mastering", "cloned_mastering", "battery_alarm",
                   "style_broken", "style_orphans", "broken_calls",
                   "remarked_positions", "remarked_logic",
                   "uninit_points", "uninit_prs",
                   "software_version", "payload_unset", "override_low", "clock_drift"]
    assert all(c["label"] and c["desc"] and c["category"] for c in healthscan.check_list())
    # categories group contiguously in registry order (the picker renders them as-is)
    cats = [c["category"] for c in healthscan.check_list()]
    seen = []
    for c in cats:
        if c not in seen:
            seen.append(c)
    assert seen == ["safety", "mastering", "programs", "positions", "config"]
    assert cats == sorted(cats, key=seen.index)
    # the parameterized check declares its input for the picker (and only it)
    by_id = {c["id"]: c for c in healthscan.check_list()}
    assert by_id["clock_drift"]["input"]["default"] == "2m"
    assert [c["id"] for c in healthscan.check_list() if c.get("input")] == ["clock_drift"]
    # de-dupes, drops unknowns, returns registry order regardless of input order
    assert healthscan.valid_ids(["mastering", "adv_dcs", "nope", "adv_dcs"]) == \
        ["adv_dcs", "mastering"]


def test_norm_queries():
    assert norm_queries("DI[279]") == ["DI[279]"]          # old single-string shape
    assert norm_queries(["  a ", "", "b", "A", None, 7]) == ["a", "b"]
    assert norm_queries(None) == []


# -- individual checks --------------------------------------------------------------

def test_adv_dcs():
    yes = _check_adv_dcs(_RobotData(FakeSession({"SUMMARY.DG": SUMMARY_ADV})))
    assert yes["status"] == "info" and "J567" in yes["detail"]
    no = _check_adv_dcs(_RobotData(FakeSession({"SUMMARY.DG": SUMMARY_PLAIN})))
    assert no["status"] == "ok"
    na = _check_adv_dcs(_RobotData(FakeSession({})))
    assert na["status"] == "na"


def test_signatures():
    bad = _check_sigs(_RobotData(FakeSession({"DCSVRFY.DG": DCSVRFY_BAD_SIG})))
    assert bad["status"] == "flag"
    assert "1 of 2" in bad["summary"]
    assert "DCS Parameter: 111222333 vs 999888777" in bad["detail"]
    good = _check_sigs(_RobotData(FakeSession({"DCSVRFY.DG": DCSVRFY_CLEAN})))
    assert good["status"] == "ok"
    assert _check_sigs(_RobotData(FakeSession({})))["status"] == "na"


def test_cip():
    off = _check_cip(_RobotData(FakeSession({"DCSVRFY.DG": DCSVRFY_BAD_SIG})))
    assert off["status"] == "flag" and "CIP Safety: DISABLE" in off["detail"]
    on = _check_cip(_RobotData(FakeSession({"DCSVRFY.DG": DCSVRFY_CLEAN})))
    assert on["status"] == "ok"


def test_mastering():
    bad = _check_mastering(_RobotData(FakeSession({"SYSMAST.VA": SYSMAST_G2_UNMASTERED})))
    assert bad["status"] == "flag" and "group 2 not mastered" in bad["summary"]
    ok = _check_mastering(_RobotData(FakeSession({"SYSMAST.VA": SYSMAST_OK})))
    assert ok["status"] == "ok"
    assert _check_mastering(_RobotData(FakeSession({})))["status"] == "na"


def test_style_checks(tmp_path):
    progs = [
        _prog(tmp_path, "S04PICK1", ["CALL S04SUB1"]),   # style root
        _prog(tmp_path, "S04SUB1", ["! leaf"]),          # reached via CALL
        _prog(tmp_path, "S05DEAD", ["! never called"]),  # S## orphan
        _prog(tmp_path, "UTIL9", ["! not an S## name"]),  # ignored by the orphan check
    ]
    ctx = _RobotData(FakeSession({"CELLIO.VA": CELLIO}, program_files=progs))
    broken = _check_style_broken(ctx)
    assert broken["status"] == "flag"
    assert "S05GONE" in broken["detail"]          # style 2 points at a missing program

    # disabled style with its program absent = placeholder, never a flag
    parked = _check_style_broken(_RobotData(
        FakeSession({"CELLIO.VA": CELLIO_PARKED}, program_files=progs)))
    assert parked["status"] == "ok"
    assert "MOVREPR2" in parked["detail"]
    orph = _check_style_orphans(ctx)
    assert orph["status"] == "info"
    assert "S05DEAD" in orph["detail"]
    assert "S04SUB1" not in orph["detail"]        # reachable from the root
    assert "UTIL9" not in orph["detail"]          # not an S## program
    # no style table at all -> n/a, never a guess
    assert _check_style_orphans(_RobotData(FakeSession({})))["status"] == "na"


# -- the job ------------------------------------------------------------------------

def _entry(tmp_path, name, line="L1"):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    return {"id": "id-" + name, "robot": name, "line": line, "plant": "P",
            "latest_path": str(d), "backups": []}


def test_job_end_to_end(tmp_path):
    r1 = _entry(tmp_path, "RB101R01B01")
    r2 = _entry(tmp_path, "RB102R01B01")
    dead = {"id": "id-dead", "robot": "RB103R01B01", "line": "L1", "plant": "P",
            "latest_path": str(tmp_path / "gone"), "backups": []}

    sessions = {
        str(tmp_path / "RB101R01B01"): FakeSession({
            "SUMMARY.DG": SUMMARY_ADV, "DCSVRFY.DG": DCSVRFY_BAD_SIG,
            "SYSMAST.VA": SYSMAST_OK,
        }),
        # identical master counts as r1 -> the cloned pair
        str(tmp_path / "RB102R01B01"): FakeSession({
            "SUMMARY.DG": SUMMARY_PLAIN, "SYSMAST.VA": SYSMAST_OK,
        }),
    }

    def search_fn(sess, query):
        return {"total": 3, "programs": [{"program": "FOO", "count": 2}],
                "io": [{"type": "DI", "index": 279}], "registers": [],
                "frames": [], "macros": [], "files": []}

    job = HealthScanJob([r1, r2, dead],
                        ["adv_dcs", "sig_mismatch", "cloned_mastering"],
                        queries=["DI[279]"],
                        session_factory=lambda p: sessions[str(p)],
                        search_fn=search_fn)
    job.run()
    snap = job.snapshot()
    assert snap["status"] == "done"
    assert snap["scanned"] == 3
    by_robot = {r["robot"]: r for r in snap["results"]}

    checks1 = {c["id"]: c for c in by_robot["RB101R01B01"]["checks"]}
    assert checks1["adv_dcs"]["status"] == "info"
    assert checks1["sig_mismatch"]["status"] == "flag"
    assert checks1["cloned_mastering"]["status"] == "flag"
    assert "RB102R01B01" in checks1["cloned_mastering"]["summary"]
    assert checks1["find:0"]["status"] == "info" and "3 hits" in checks1["find:0"]["summary"]

    checks2 = {c["id"]: c for c in by_robot["RB102R01B01"]["checks"]}
    assert checks2["adv_dcs"]["status"] == "ok"
    assert checks2["sig_mismatch"]["status"] == "na"      # no DCS report
    assert checks2["cloned_mastering"]["status"] == "flag"

    # the unopenable robot: every requested row (incl. find) n/a, and exactly
    # ONE cloned_mastering row (the all-na path must not double up in the pass)
    rows3 = by_robot["RB103R01B01"]["checks"]
    assert {c["id"] for c in rows3} == {"adv_dcs", "sig_mismatch", "cloned_mastering", "find:0"}
    assert all(c["status"] == "na" for c in rows3)
    assert sum(1 for c in rows3 if c["id"] == "cloned_mastering") == 1
    # the transient clone vector never leaks into the payload
    assert all("_mast" not in r for r in snap["results"])


def test_job_cancel(tmp_path):
    r1 = _entry(tmp_path, "RB101R01B01")
    job = HealthScanJob([r1], ["adv_dcs"], session_factory=lambda p: FakeSession({}))
    job.cancel()
    job.run()
    assert job.snapshot()["status"] == "cancelled"


def test_job_no_clones_when_counts_differ(tmp_path):
    r1 = _entry(tmp_path, "RB101R01B01")
    r2 = _entry(tmp_path, "RB102R01B01")
    sessions = {
        str(tmp_path / "RB101R01B01"): FakeSession({"SYSMAST.VA": SYSMAST_OK}),
        str(tmp_path / "RB102R01B01"): FakeSession({"SYSMAST.VA": SYSMAST_G2_UNMASTERED}),
    }
    job = HealthScanJob([r1, r2], ["cloned_mastering"],
                        session_factory=lambda p: sessions[str(p)])
    job.run()
    snap = job.snapshot()
    assert snap["status"] == "done"
    for r in snap["results"]:
        row = next(c for c in r["checks"] if c["id"] == "cloned_mastering")
        assert row["status"] == "ok"


# -- round-3 checks -----------------------------------------------------------------

def _alarm(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_sw_version_check():
    row = _check_sw_version(_RobotData(FakeSession({"SUMMARY.DG": SUMMARY_V833})))
    assert row["status"] == "info"
    assert row["summary"] == "V8.33P/16"                  # techs quote editions
    assert "version V8.33" in row["detail"]
    assert "servo V26.14" in row["detail"]
    assert "DCS V3.5.21" in row["detail"]
    # SUMMARY present but carrying no version block -> n/a, never a guess
    novers = _check_sw_version(_RobotData(FakeSession({"SUMMARY.DG": SUMMARY_ADV})))
    assert novers["status"] == "na"
    assert _check_sw_version(_RobotData(FakeSession({})))["status"] == "na"


def test_version_drift_within_line(tmp_path):
    r1 = _entry(tmp_path, "RB101R01B01", "L1")
    r2 = _entry(tmp_path, "RB102R01B01", "L1")
    r3 = _entry(tmp_path, "RB103R01B01", "L1")
    r4 = _entry(tmp_path, "RB204R01B01", "L2")   # other line runs old edition uniformly
    dead = {"id": "id-dead", "robot": "RB105R01B01", "line": "L1", "plant": "P",
            "latest_path": str(tmp_path / "gone"), "backups": []}
    sessions = {
        str(tmp_path / "RB101R01B01"): FakeSession({"SUMMARY.DG": SUMMARY_V833}),
        str(tmp_path / "RB102R01B01"): FakeSession({"SUMMARY.DG": SUMMARY_V833}),
        str(tmp_path / "RB103R01B01"): FakeSession({"SUMMARY.DG": SUMMARY_V830}),
        str(tmp_path / "RB204R01B01"): FakeSession({"SUMMARY.DG": SUMMARY_V830}),
    }
    job = HealthScanJob([r1, r2, r3, r4, dead], ["software_version"],
                        session_factory=lambda p: sessions[str(p)])
    job.run()
    snap = job.snapshot()
    assert snap["status"] == "done"
    rows = {}
    for r in snap["results"]:
        got = [c for c in r["checks"] if c["id"] == "software_version"]
        assert len(got) == 1                     # fleet pass never doubles a row
        rows[r["robot"]] = got[0]
    assert rows["RB101R01B01"]["status"] == "info"        # majority stays info
    assert rows["RB102R01B01"]["status"] == "info"
    assert rows["RB103R01B01"]["status"] == "flag"        # the odd one out
    assert "rest of L1 runs V8.33P/16" in rows["RB103R01B01"]["summary"]
    assert "V8.30P/12" in rows["RB103R01B01"]["summary"]
    assert rows["RB204R01B01"]["status"] == "info"        # lines never cross
    assert rows["RB105R01B01"]["status"] == "na"          # unopenable stays n/a
    # the transient edition never leaks into the payload
    assert all("_swv" not in r for r in snap["results"])


def test_version_drift_even_split_flags_both(tmp_path):
    r1 = _entry(tmp_path, "RB101R01B01", "L1")
    r2 = _entry(tmp_path, "RB102R01B01", "L1")
    sessions = {
        str(tmp_path / "RB101R01B01"): FakeSession({"SUMMARY.DG": SUMMARY_V833}),
        str(tmp_path / "RB102R01B01"): FakeSession({"SUMMARY.DG": SUMMARY_V830}),
    }
    job = HealthScanJob([r1, r2], ["software_version"],
                        session_factory=lambda p: sessions[str(p)])
    job.run()
    for r in job.snapshot()["results"]:
        row = next(c for c in r["checks"] if c["id"] == "software_version")
        assert row["status"] == "flag"           # drift certain, odd one out isn't
        assert "L1 is split" in row["summary"]


def test_battery_alarm(tmp_path):
    hit = _check_battery(_RobotData(FakeSession(
        {}, alarm_files=[_alarm(tmp_path, "ERRALL.LS", ERRALL_BLAL)])))
    assert hit["status"] == "flag"
    assert "×1" in hit["summary"] and "ACTIVE" in hit["summary"]
    assert "SRVO-065" in hit["detail"] and "09-JUN-26 16:17:38" in hit["detail"]

    quiet = _check_battery(_RobotData(FakeSession(
        {}, alarm_files=[_alarm(tmp_path, "ERRACT.LS", ERRALL_QUIET)])))
    assert quiet["status"] == "ok"               # SRVO-037 is not a battery code

    none = _check_battery(_RobotData(FakeSession({})))
    assert none["status"] == "na"

    # ERRALL repeats what ERRHIST carries - the same alarm never counts twice
    both = _check_battery(_RobotData(FakeSession({}, alarm_files=[
        _alarm(tmp_path, "ERRALL2.LS", ERRALL_BLAL),
        _alarm(tmp_path, "ERRHIST.LS", ERRALL_BLAL),
    ])))
    assert both["status"] == "flag" and "×1" in both["summary"]


def test_payload_unset():
    bad = _check_payload(_RobotData(FakeSession({"SYMOTN.VA": SYMOTN_UNSET})))
    assert bad["status"] == "flag"
    assert "no payload schedule is set" in bad["summary"]
    assert "all 2 schedules" in bad["detail"]

    ok = _check_payload(_RobotData(FakeSession({"SYMOTN.VA": SYMOTN_SET})))
    assert ok["status"] == "ok"
    assert "1 of 2 schedules set" in ok["summary"]
    assert "EOAT" in ok["detail"]

    # a changed mass counts as configured even without a comment/CG
    massonly = _check_payload(_RobotData(FakeSession({"SYMOTN.VA": SYMOTN_MASS_ONLY})))
    assert massonly["status"] == "ok"
    assert "1 of 2" in massonly["summary"]

    assert _check_payload(_RobotData(FakeSession({})))["status"] == "na"
    # SYMOTN present but no $PLST_GRP records -> n/a, not a flag
    norec = _check_payload(_RobotData(FakeSession({"SYMOTN.VA": SYSMAST_OK})))
    assert norec["status"] == "na"


def test_broken_calls(tmp_path):
    progs = [
        _prog(tmp_path, "MAIN1", ["CALL GONE1", "IF R[1]=1,CALL GONE1", "RUN GONE2",
                                  "CALL S04SUB1", "CALL BINPROG", "CALL KARELP"]),
        _prog(tmp_path, "S04SUB1", ["! leaf"]),
    ]
    ctx = _RobotData(FakeSession({"GONE2.TP": "", "BINPROG.TP": ""},
                                 program_files=progs, karel=["KARELP"]))
    row = _check_broken_calls(ctx)
    assert row["status"] == "info"
    assert "1 called program not in the backup" in row["summary"]
    assert "(2 call sites)" in row["summary"]     # GONE1 called twice from MAIN1
    assert "GONE1" in row["detail"] and "MAIN1" in row["detail"]
    assert "GONE2" not in row["detail"]           # RUN target present as a binary .TP
    assert "BINPROG" not in row["detail"]         # present as a binary .TP
    assert "KARELP" not in row["detail"]          # present as a KAREL program
    assert "S04SUB1" not in row["detail"]         # present as source

    clean = _check_broken_calls(_RobotData(FakeSession(
        {}, program_files=[_prog(tmp_path, "MAIN2", ["CALL SUB2"]),
                           _prog(tmp_path, "SUB2", ["! leaf"])])))
    assert clean["status"] == "ok"

    assert _check_broken_calls(_RobotData(FakeSession({})))["status"] == "na"


def test_multi_query_find(tmp_path):
    r1 = _entry(tmp_path, "RB101R01B01")
    dead = {"id": "id-dead", "robot": "RB103R01B01", "line": "L1", "plant": "P",
            "latest_path": str(tmp_path / "gone"), "backups": []}
    calls = []

    def search_fn(sess, query):
        calls.append(query)
        if query == "DI[1]":
            return {"total": 2, "programs": [{"program": "FOO", "count": 2}],
                    "io": [], "registers": [], "frames": [], "macros": [], "files": []}
        return {"total": 0}

    job = HealthScanJob([r1, dead], [], queries=["DI[1]", "R[5]", "di[1]", "  "],
                        session_factory=lambda p: FakeSession({}),
                        search_fn=search_fn)
    assert job.queries == ["DI[1]", "R[5]"]       # dupes + blanks dropped
    job.run()
    snap = job.snapshot()
    assert snap["status"] == "done"
    assert calls == ["DI[1]", "R[5]"]             # each query ran once on the live robot
    by_robot = {r["robot"]: r for r in snap["results"]}

    live = {c["id"]: c for c in by_robot["RB101R01B01"]["checks"]}
    assert live["find:0"]["status"] == "info" and "2 hits" in live["find:0"]["summary"]
    assert live["find:1"]["status"] == "ok"

    rows_dead = by_robot["RB103R01B01"]["checks"]
    assert {c["id"] for c in rows_dead} == {"find:0", "find:1"}
    assert all(c["status"] == "na" for c in rows_dead)


# -- positions / remarks / config checks --------------------------------------------

def _lsfile(tmp_path, name, text):
    p = tmp_path / (name + ".LS")
    p.write_text(text, encoding="utf-8")
    return p


def test_remarked_positions_and_logic(tmp_path):
    progs = [_lsfile(tmp_path, "TESTPTS", LS_POINTS),
             _lsfile(tmp_path, "LOGICP", LS_LOGIC)]
    ctx = _RobotData(FakeSession({}, program_files=progs))

    pos = _check_remarked_positions(ctx)
    assert pos["status"] == "flag"
    assert "1 remarked motion line" in pos["summary"]
    assert "TESTPTS line 3: //J P[3] 50% FINE" in pos["detail"]
    assert "LOGICP" not in pos["detail"]          # logic remarks are the other check's

    logic = _check_remarked_logic(ctx)
    assert logic["status"] == "info"               # often a deliberate standard - never red
    assert "2 remarked logic lines" in logic["summary"]
    assert "//CALL GET_HOME" in logic["detail"]
    assert "//UTOOL_NUM=1" in logic["detail"]
    assert "TESTPTS" not in logic["detail"]        # the motion remark stays out

    clean = _RobotData(FakeSession({}, program_files=[
        _lsfile(tmp_path, "CLEANPTS", LS_POINTS_OK)]))
    assert _check_remarked_positions(clean)["status"] == "ok"
    assert _check_remarked_logic(clean)["status"] == "ok"
    assert _check_remarked_positions(_RobotData(FakeSession({})))["status"] == "na"


def test_uninit_points(tmp_path):
    ctx = _RobotData(FakeSession({}, program_files=[
        _lsfile(tmp_path, "TESTPTS", LS_POINTS)]))
    row = _check_uninit_points(ctx)
    assert row["status"] == "flag"
    assert "2 referenced positions with no recorded data" in row["summary"]
    assert "P[2] (line 2)" in row["detail"]
    assert "P[4] (line 6)" in row["detail"]        # the circular's CONTINUATION point
    assert "P[3]" not in row["detail"]             # remarked ref never counts
    assert "P[9]" not in row["detail"]             # comment mention never counts
    assert "1 indirect P[R[..]] ref not checkable" in row["detail"]

    ok = _check_uninit_points(_RobotData(FakeSession({}, program_files=[
        _lsfile(tmp_path, "CLEANPTS", LS_POINTS_OK)])))
    assert ok["status"] == "ok"
    assert _check_uninit_points(_RobotData(FakeSession({})))["status"] == "na"


def test_uninit_prs(tmp_path):
    reader = [_lsfile(tmp_path, "PRMOVE", LS_PR)]
    ctx = _RobotData(FakeSession({"POSREG.VA": POSREG_MIX}, program_files=reader))
    row = _check_uninit_prs(ctx)
    assert row["status"] == "flag"                 # nothing writes PR[2]/PR[7]
    assert "read 2 uninitialized PRs" in row["summary"]
    assert "PR[2] 'Home2'" in row["detail"] and "PR[7] 'Spare'" in row["detail"]
    assert "PRMOVE" in row["detail"]
    assert "1 indirect/group-prefixed PR ref not checkable" in row["detail"]

    # a writer somewhere demotes the read to info - it may be set at runtime
    both = _RobotData(FakeSession({"POSREG.VA": POSREG_MIX}, program_files=[
        _lsfile(tmp_path, "PRMOVE2", LS_PR.replace("PRMOVE", "PRMOVE2")),
        _lsfile(tmp_path, "INITPR", LS_PR_INIT)]))
    demoted = _check_uninit_prs(both)
    assert demoted["status"] == "info"
    assert "written by some program" in demoted["summary"]
    assert "written by INITPR" in demoted["detail"]

    # writes alone are not reads
    writer_only = _RobotData(FakeSession({"POSREG.VA": POSREG_MIX}, program_files=[
        _lsfile(tmp_path, "INITPR2", LS_PR_INIT.replace("INITPR", "INITPR2"))]))
    quiet = _check_uninit_prs(writer_only)
    assert quiet["status"] == "ok"
    assert "none read" in quiet["summary"]

    assert _check_uninit_prs(_RobotData(FakeSession({})))["status"] == "na"
    noprogs = _check_uninit_prs(_RobotData(FakeSession({"POSREG.VA": POSREG_MIX})))
    assert noprogs["status"] == "na"


def test_override():
    ok = _check_override(_RobotData(FakeSession({"SYCLDINT.VA": SYCLDINT_100})))
    assert ok["status"] == "ok" and ok["summary"] == "100%"
    low = _check_override(_RobotData(FakeSession({"SYCLDINT.VA": SYCLDINT_55})))
    assert low["status"] == "flag"
    assert "general override at 55%" in low["summary"]
    # the $CONDET string that merely NAMES the variable must never match
    trap = _check_override(_RobotData(FakeSession({"SYCLDINT.VA": SYCLDINT_TRAP})))
    assert trap["status"] == "na"
    assert _check_override(_RobotData(FakeSession({})))["status"] == "na"


def test_parse_tolerance():
    assert _parse_tolerance("30s") == (30, True)
    assert _parse_tolerance("2m") == (120, True)
    assert _parse_tolerance("1m30s") == (90, True)
    assert _parse_tolerance("1h") == (3600, True)
    assert _parse_tolerance("5") == (300, True)        # bare number reads as minutes
    assert _parse_tolerance("") == (120, False)        # blank -> default, says so
    assert _parse_tolerance("junk") == (120, False)
    assert _parse_tolerance(None) == (120, False)


def _clock_session(tmp_path, name, content, controller_dt, offset_s):
    """A session whose file mtime sits offset_s seconds BEFORE the controller
    stamp inside it - i.e. the controller runs offset_s ahead of the PC."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    t = controller_dt.timestamp() - offset_s
    os.utime(p, (t, t))
    return FakeSession({name: content}, paths={name: p})


def test_clock_drift(tmp_path):
    ctrl = datetime(2026, 6, 11, 8, 55, 56)        # the stamp inside BACKDATE

    within = _check_clock(_RobotData(
        _clock_session(tmp_path, "BACKDATE.DT", BACKDATE, ctrl, 52)))
    assert within["status"] == "ok"                # +52s inside the 2m default
    assert "drift +52s" in within["summary"] and "2m (default)" in within["summary"]
    assert "BACKDATE.DT" in within["detail"]

    over = _check_clock(_RobotData(
        _clock_session(tmp_path, "BACKDATE.DT", BACKDATE, ctrl, 52)), "30s")
    assert over["status"] == "flag"                # same drift, tighter tolerance
    assert "off by +52s" in over["summary"] and "allowed 30s" in over["summary"]

    behind = _check_clock(_RobotData(
        _clock_session(tmp_path, "BACKDATE.DT", BACKDATE, ctrl, -400)), "5m")
    assert behind["status"] == "flag"
    assert "off by -6m40s" in behind["summary"]

    # no BACKDATE.DT: the DG head carries the stamp at minute resolution
    dg = _check_clock(_RobotData(_clock_session(
        tmp_path, "SUMMARY.DG", SUMMARY_CLOCK, datetime(2026, 6, 11, 8, 56, 0), 30)))
    assert dg["status"] == "ok"
    assert "minute-resolution" in dg["detail"]

    # a session that can't hand back a real path (no mtime) stays honest
    assert _check_clock(_RobotData(FakeSession({"BACKDATE.DT": BACKDATE})))["status"] == "na"
    assert _check_clock(_RobotData(FakeSession({})))["status"] == "na"


def test_job_param_passthrough(tmp_path):
    r1 = _entry(tmp_path, "RB101R01B01")
    ctrl = datetime(2026, 6, 11, 8, 55, 56)
    sess = _clock_session(tmp_path, "BACKDATE.DT", BACKDATE, ctrl, 52)

    tight = HealthScanJob([r1], ["clock_drift"], params={"clock_drift": "30s"},
                          session_factory=lambda p: sess)
    tight.run()
    row = next(c for c in tight.snapshot()["results"][0]["checks"]
               if c["id"] == "clock_drift")
    assert row["status"] == "flag" and "allowed 30s" in row["summary"]

    default = HealthScanJob([r1], ["clock_drift"], session_factory=lambda p: sess)
    default.run()
    row = next(c for c in default.snapshot()["results"][0]["checks"]
               if c["id"] == "clock_drift")
    assert row["status"] == "ok" and "2m (default)" in row["summary"]
