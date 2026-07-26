"""Identity-normalized program alignment (compare.align_program_lines
normalize mode) - the pane-vs-pane diff's correctness core.

Fully synthetic and identifier-clean (test_compare.py is git-excluded with the
real-backup fixtures, so this coverage lives here). The domain fact under
test: a ref's bracket tail - R[21:SERVO GUN WORK]'s comment, and the third
field the pendant's IO-status view adds (DI[10:OFF:Comment]) - is SAVE-TIME
DISPLAY STATE, not part of the instruction. Two robots routinely save the
same line under different display state; a raw text diff would flag every IO
line as changed while nothing semantically differs. Wrong data erodes trust
worse than missing data, so those rows classify 'equiv', never 'change'.
"""
from backupviewer import compare


def test_align_program_lines_normalize_equiv():
    a = [{"n": 1, "text": "R[21]=1"},
         {"n": 2, "text": "DO[10:Clamp]=ON"},
         {"n": 3, "text": "WAIT DI[3:OFF:PartPresent]=ON"},
         {"n": 4, "text": "END"}]
    b = [{"n": 1, "text": "R[21:SERVO GUN WORK]=1"},
         {"n": 2, "text": "DO[10:ON:Clamp]=ON"},
         {"n": 3, "text": "WAIT DI[3:PartPresent]=ON"},
         {"n": 4, "text": "END"}]
    raw = compare.align_program_lines(a, b)
    assert raw["stats"]["same"] == 1              # the naive view: 3 lines "changed"
    out = compare.align_program_lines(a, b, normalize=True)
    assert [r["kind"] for r in out["rows"]] == ["equiv", "equiv", "equiv", "same"]
    # raw text preserved on both sides - classification changes, display never
    assert out["rows"][0]["a_text"] == "R[21]=1"
    assert out["rows"][0]["b_text"] == "R[21:SERVO GUN WORK]=1"
    assert out["stats"] == {"same": 1, "equiv": 3, "change": 0, "a_only": 0, "b_only": 0}


def test_align_normalize_keeps_real_changes():
    a = [{"n": 1, "text": "DO[10:Clamp]=ON"}, {"n": 2, "text": "R[5]=1"}]
    b = [{"n": 1, "text": "DO[11:ON:Clamp]=ON"}, {"n": 2, "text": "R[5]=2"}]
    out = compare.align_program_lines(a, b, normalize=True)
    # different index / different value: real changes survive normalization
    assert [r["kind"] for r in out["rows"]] == ["change", "change"]


def test_align_normalize_aligns_shifted_equiv():
    # an inserted line must not stop equivalent lines from pairing up: the
    # alignment itself runs on normalized keys, not just the classification
    a = [{"n": 1, "text": "DO[1:Clamp]=ON"}, {"n": 2, "text": "END"}]
    b = [{"n": 1, "text": "!new remark"},
         {"n": 2, "text": "DO[1:OFF:Clamp]=ON"}, {"n": 3, "text": "END"}]
    out = compare.align_program_lines(a, b, normalize=True)
    assert [r["kind"] for r in out["rows"]] == ["b_only", "equiv", "same"]


def test_normalize_tp_refs():
    f = compare.normalize_tp_refs
    assert f("DI[10:OFF:PartPresent]") == "DI[10]"
    assert f("R[21:SERVO GUN WORK]=R[21]") == "R[21]=R[21]"
    assert f("DO[R[2:IDX]]=ON") == "DO[R[2]]=ON"      # nested inner ref
    assert f("PR[GP1:1]") == "PR[GP1:1]"             # group-prefixed head: untouched
    assert f("J P[1:HOME] 100% FINE") == "J P[1] 100% FINE"


def test_io_status_shown():
    # the toggle is whole-program save-time state: one 3-field ref means ON
    assert compare.io_status_shown("WAIT DI[3:OFF:Part]=ON")
    assert not compare.io_status_shown("WAIT DI[3:Part]=ON\nR[21:CNT]=1")
