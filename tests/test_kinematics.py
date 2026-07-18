"""roboguidedef + kinematics + curpos: the pose pipeline, synthetic only.

The def fixture mirrors the real RobotCadFolder shape with fabricated
dimensions; FK expectations are hand-derived. The real-controller
validation (CURPOS cross-checks on live backups) lives in the untracked
probe - these tests pin the math and the parsing.
"""
import math

from backupviewer.parsers import curpos, kinematics, roboguidedef

_DEF = """<?xml version="1.0" encoding="utf-8"?>
<RobotCadFolder Version="10.0">
  <General ZeroOffset="0,0,400,0,0,0" BodyColor="FFFF" InitJointAngles="0,0,0,0,-90,0">
    <DesignTime TraceTreeOn="true" />
  </General>
  <RobotCadFile CadFileName="FakeBot-3d.rcf">
    <RobotUnit>
      <UnitLink Name="Base" Type="Axis" CadUnitIndex="0">
        <UnitLink Name="J1-Axis" Type="Axis" JointNumber="1" CadUnitIndex="1">
          <OffsetCADToAxis Z="400" />
          <UnitLink Name="2D Envelope" Type="Machine" Dress="workenvelope" JointNumber="1" CadFileName="%ROBOTLIBRARY%\\WorkEnvelopes\\RANGE_Fake_Bot_10.rcf" Visible="false" CollisionGroup="false" />
          <UnitLink Name="J2-Axis" Type="Axis" JointNumber="2" CadUnitIndex="2" NegDirection="true">
            <OffsetCADToAxis X="100" Z="400" W="90" />
            <UnitLink Name="J3-Axis" Type="Axis" JointNumber="3" CadUnitIndex="3">
              <OffsetCADToAxis X="100" Z="1000" P="-90" R="90" />
              <ParallelLink JointNumber="2" />
              <UnitLink Name="J4-Axis" Type="Axis" JointNumber="4" CadUnitIndex="4">
                <OffsetCADToAxis X="100" Z="1200" P="-90" />
                <UnitLink Name="J5-Axis" Type="Axis" JointNumber="5" CadUnitIndex="5">
                  <OffsetCADToAxis X="700" Z="1200" P="-90" R="90" />
                  <UnitLink Name="J6-Axis" Type="Axis" JointNumber="6" CadUnitIndex="6">
                    <OffsetCADToAxis X="700" Z="1200" P="-90" />
                    <UnitLink Name="Face Plate" Type="FacePlate">
                      <OffsetPost X="800" Z="1200" W="180" P="-90" />
                    </UnitLink>
                  </UnitLink>
                </UnitLink>
              </UnitLink>
            </UnitLink>
          </UnitLink>
        </UnitLink>
      </UnitLink>
    </RobotUnit>
  </RobotCadFile>
</RobotCadFolder>
"""


def _kin():
    return roboguidedef.parse_def(_DEF)


def test_def_parse_shape():
    k = _kin()
    assert k["zero"] == [0, 0, 400, 0, 0, 0]
    assert k["init_angles"] == [0, 0, 0, 0, -90, 0]
    assert k["envelope_name"] == "Fake Bot 10"
    assert [j["n"] for j in k["joints"]] == [1, 2, 3, 4, 5, 6]
    j2, j3 = k["joints"][1], k["joints"][2]
    assert j2["neg"] and j2["p"] == [100, 0, 400] and j2["wpr"] == [90, 0, 0]
    assert j3["parallel"] == 2 and not j3["neg"]
    assert k["faceplate"] == {"p": [800, 0, 1200], "wpr": [180, -90, 0]}
    # dress/envelope UnitLinks (no CadUnitIndex) are not joints
    assert all(j["p"][2] >= 400 for j in k["joints"])


def test_def_rejects_non_robot():
    try:
        roboguidedef.parse_def("<RobotCadFolder><General/></RobotCadFolder>")
        raise AssertionError("should have raised")
    except ValueError:
        pass


def test_normalize_and_filename():
    n = roboguidedef.normalize_type
    assert n("R-2000iC/210F-IF") == "R2000IC210FIF"
    assert n("R2000iC_210F") == "R2000IC210F"
    assert n("ARC Mate 120iD") == n("ARCMate120iD")
    assert roboguidedef.def_name_from_filename("R2000iC_210F-3d_NEW.def") == "R2000iC_210F"
    assert roboguidedef.def_name_from_filename("CRX-30iA-3d_NEW.def") == "CRX-30iA"


def _pos(mat):
    return [round(mat[i][3], 6) for i in range(3)]


def test_chain_home_pose():
    """q = 0 places every frame at its def position (world = CAD - zero)."""
    f = kinematics.chain_frames(_kin(), [0, 0, 0, 0, 0, 0])
    assert _pos(f["joints"][0]) == [0, 0, 0]          # J1 at world origin
    assert _pos(f["joints"][1]) == [100, 0, 0]
    assert _pos(f["joints"][2]) == [100, 0, 600]
    assert _pos(f["faceplate"]) == [800, 0, 800]
    # faceplate home orientation: local Z points along world +X
    assert [round(f["faceplate"][i][2], 6) for i in range(3)] == [1, 0, 0]


def test_chain_j1_rotation():
    """J1 +90 swings the whole arm about world Z: x -> y."""
    f = kinematics.chain_frames(_kin(), [90, 0, 0, 0, 0, 0])
    assert _pos(f["faceplate"]) == [0, 800, 800]


def test_chain_j2_negdirection():
    """Pendant J2 +90 with NegDirection tips the arm FORWARD (+X): the
    600-tall lower arm above the shoulder lays down along +X."""
    f = kinematics.chain_frames(_kin(), [0, 90, 0, 0, 0, 0])
    # shoulder stays; elbow (was 600 above shoulder) now 600 in front
    assert _pos(f["joints"][1]) == [100, 0, 0]
    assert _pos(f["joints"][2]) == [700, 0, 0]
    # parallel-link: pendant J3 rides along (physical theta3 = q3 + q2),
    # so the forearm stays PARALLEL to its home attitude - the wrist sits
    # at elbow + its home lever (600 fwd, 200 up)
    assert _pos(f["joints"][4]) == [1300, 0, 200]


def test_chain_flange_dz():
    """The measured adapter-plate correction extends along faceplate Z."""
    f = kinematics.chain_frames(_kin(), [0, 0, 0, 0, 0, 0], flange_dz=25.0)
    assert _pos(f["faceplate"]) == [825, 0, 800]


def test_measure_flange_recovers_offset():
    """Inject a +23mm plate + tool into a synthetic 'controller report' and
    the measurement recovers it as a clean pure-Z hit."""
    kin = _kin()
    q = [10.0, -20.0, 30.0, 40.0, -50.0, 60.0]
    tool = [11.0, -22.0, 333.0, 0.0, -90.0, 5.0]
    posed = kinematics.chain_frames(kin, q, flange_dz=23.0)
    tcp = kinematics.mul(posed["faceplate"], kinematics.frame(tool[:3], tool[3:]))
    world = [tcp[0][3], tcp[1][3], tcp[2][3]] + kinematics.wpr_of(tcp)
    m = kinematics.measure_flange(kin, q, tool, world)
    assert m["ok"] and abs(m["dz"] - 23.0) < 1e-6 and m["dxy"] < 1e-6
    # and a wrong arm does NOT calibrate: bend a link by 50mm
    bad = roboguidedef.parse_def(_DEF.replace('X="700" Z="1200" P="-90" R="90"',
                                              'X="750" Z="1200" P="-90" R="90"'))
    m2 = kinematics.measure_flange(bad, q, tool, world)
    assert not m2["ok"]


_CURPOS = """F Number: F999999
VERSION : SpotTool+
$VERSION: V8.33258     10/28/2024
DATE:     12-JUN-26 08:48

CURRENT ROBOT POSITION::
Group #:  1

CURRENT JOINT POSITION:
Joint   1:      -.00
Joint   2:    -10.00
Joint   3:     40.00
Joint   4:      5.00
Joint   5:    -90.00
Joint   6:    -95.00


Frame #:  0  Tool #:  1
CURRENT USER FRAME POSITION:
CFG: N U T, 0, 0, 0
X:    111.11
Y:    222.22
Z:    333.33
W:     44.44
P:      5.55
R:     66.66


Tool #:  1
CURRENT WORLD POSITION:
CFG: N U T, 0, 0, 0
X:   2032.71
Y:    -70.80
Z:   1840.86
W:   -139.75
P:      0.99
R:    -98.93
"""

_FRAME = """F Number: F999999
DATE:     12-JUN-26 08:48

Tool Frame
 -63.3  -681.0   616.7     0.0     0.0     2.5 PH06 PIN
   0.0     0.0     0.0     0.0     0.0     0.0 Eoat2
   0.0     0.0     0.0     0.0     0.0     0.0

Jog Frame
   0.0     0.0     0.0     0.0     0.0     0.0
"""


def test_curpos_parse():
    c = curpos.parse_curpos(_CURPOS)
    assert c["date"] == "12-JUN-26 08:48"
    g = c["groups"][0]
    assert g["group"] == 1 and g["tool"] == 1
    assert g["joints"] == [-0.0, -10.0, 40.0, 5.0, -90.0, -95.0]
    # the world block wins over the user-frame block
    assert g["world"] == [2032.71, -70.80, 1840.86, -139.75, 0.99, -98.93]


def test_tool_frames_parse():
    tools = curpos.parse_tool_frames(_FRAME)
    assert len(tools) == 3
    assert tools[0]["xyzwpr"] == [-63.3, -681.0, 616.7, 0.0, 0.0, 2.5]
    assert tools[0]["comment"] == "PH06 PIN" and tools[0]["n"] == 1
    assert tools[2]["comment"] == ""


def test_full_pipeline_on_synthetic_truth():
    """def + curpos + tool -> measured flange, round-tripped end to end."""
    kin = _kin()
    c = curpos.parse_curpos(_CURPOS)["groups"][0]
    tool = [-63.3, -681.0, 616.7, 0.0, 0.0, 2.5]
    posed = kinematics.chain_frames(kin, c["joints"], flange_dz=10.0)
    tcp = kinematics.mul(posed["faceplate"], kinematics.frame(tool[:3], tool[3:]))
    world = [tcp[i][3] for i in range(3)] + kinematics.wpr_of(tcp)
    m = kinematics.measure_flange(kin, c["joints"], tool, world)
    assert m["ok"] and abs(m["dz"] - 10.0) < 1e-6
