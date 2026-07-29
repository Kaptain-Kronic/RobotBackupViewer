from backupviewer.parsers import payloads

# group 1: a real schedule (EOAT) + a default slot (100 kg, no comment/cg/inertia)
# group 2: a sentinel placeholder for a group the robot doesn't have (-9999)
_PL = (
    "[*SYSTEM*]$PLST_GRP1  Storage: CMOS  Access: RO  : ARRAY[10] OF PLST_GRP_T\n"
    "     Field: $PLST_GRP1[1].$COMMENT Access: RO: STRING[17] = 'EOAT'\n"
    "     Field: $PLST_GRP1[1].$PAYLOAD Access: RO: REAL = 1.000000e+01\n"
    "     Field: $PLST_GRP1[1].$PAYLOAD_X Access: RO: REAL = 0.000000e+00\n"
    "     Field: $PLST_GRP1[1].$PAYLOAD_Y Access: RO: REAL = 2.000000e-01\n"
    "     Field: $PLST_GRP1[1].$PAYLOAD_Z Access: RO: REAL = 1.500000e+01\n"
    "     Field: $PLST_GRP1[1].$PAYLOAD_IX Access: RO: REAL = 4.190480e+03\n"
    "     Field: $PLST_GRP1[1].$PAYLOAD_IY Access: RO: REAL = 4.179700e+03\n"
    "     Field: $PLST_GRP1[1].$PAYLOAD_IZ Access: RO: REAL = 2.097200e+02\n"
    "     Field: $PLST_GRP1[2].$COMMENT Access: RO: STRING[17] = ''\n"
    "     Field: $PLST_GRP1[2].$PAYLOAD Access: RO: REAL = 1.000000e+02\n"
    "     Field: $PLST_GRP1[2].$PAYLOAD_X Access: RO: REAL = 0.000000e+00\n"
    "     Field: $PLST_GRP1[2].$PAYLOAD_IX Access: RO: REAL = 0.000000e+00\n"
    "[*SYSTEM*]$PLST_GRP2  Storage: CMOS  Access: RO  : ARRAY[1] OF PLST_GRP_T\n"
    "     Field: $PLST_GRP2[1].$COMMENT Access: RO: STRING[17] = ''\n"
    "     Field: $PLST_GRP2[1].$PAYLOAD Access: RO: REAL = -9.999000e+03\n"
)


def test_payloads_schedule_fields():
    m = payloads.build_payloads_model(_PL)
    assert set(m["groups"]) == {"1", "2"}
    s1 = next(s for s in m["groups"]["1"] if s["index"] == 1)
    assert s1["comment"] == "EOAT"
    assert s1["mass"] == 10.0
    assert s1["cg"] == [0.0, 0.2, 15.0]
    assert s1["inertia"] == [4190.48, 4179.7, 209.72]
    assert s1["uninit"] is False


def test_payloads_uninit_and_sentinel():
    m = payloads.build_payloads_model(_PL)
    # default slot: 100 kg, no comment / CG / inertia -> not a real schedule
    s2 = next(s for s in m["groups"]["1"] if s["index"] == 2)
    assert s2["uninit"] is True
    # sentinel group: -9999 mass normalised to None, schedule flagged empty
    g2 = m["groups"]["2"][0]
    assert g2["mass"] is None and g2["uninit"] is True
