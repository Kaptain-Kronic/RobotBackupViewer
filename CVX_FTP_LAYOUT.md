# Keyence CV-X482D FTP layout (captured live 2026-07-13, .55)

- banner: `220 CV-X482D (6.0.0000) FTP server ready.`
- **anonymous FTP login works** (empty user/pass); lands at `/SD1/`
- `/SD1/cv-x/setting/` = config (env.dat, RBT_G_RMD/LYT `.dat`/`.tbd`, recovery/, numbered program dirs) -> backup target
- `/SD1/cv-x/box/` = saved sets: BOX_SD1_001_T100, BOX_SD1_001_T190, BOX_SD1_001_T101, BOX_SD1_001_T101_deep
- `/SD1/cv-x/temp/` = empty
- FTP quirk: `LIST <path>` returns 550; must CWD then bare LIST. Paths are relative; login dir = /SD1.
- setting/ file types: {'dat': 5, 'bmp': 2, 'tbd': 1}

- `/SD1/cv-x/workspace/<NAME>/` = **simulator workspaces stored on the camera itself**
  (`workspace.xml` + a nested `SD1/cv-x/setting/` tree). Not currently pulled.

=> Keyence backup is plain FTP (no Vapi.Net.dll needed): pull /SD1/cv-x/setting (+ optionally box). Mirrors mtxbackup.py.

## Simulator workspace layout (2026-07-25)

The CV-X Series Simulator opens a folder shaped exactly like our pull, one level down:

```
<WorkspaceName>/
  workspace.xml          <- the manifest (see keyence_workspace.py)
  SD1/cv-x/setting/**    <- the same tree the FTP pull already returns
```

So the backup lands at `<dated>/<label>/SD1/cv-x/setting/…` + `<label>/workspace.xml`
and every CV-X backup is directly openable — no export step.

Verified end to end 2026-07-25: replaying the real KeyenceBackupJob against three
simulator-made workspaces reproduced all three `SD1/` trees byte-for-byte (41/41,
41/41, 25/25 files) and two of the three `workspace.xml` byte-for-byte. The third
differed *only* in the remembered window geometry (`SimulatorSize*`/`Position*`,
`ImageBar*`) — fields a freshly-created workspace writes as `-1` and the simulator
fills in the first time you move its window.

Identity fields, and what can be proved: `env.dat` carries **neither** the IP nor
the controller type/grade (searched, three cameras), so the IP has to come from
the job that dialed the camera. All 50 real-camera samples report ControllerType
`12` and SoftwareGrade `1078219315` — which is packed ASCII, `"@DR3"` big-endian
(the simulator's own new-workspace default is `1078021196` = `"@ALL"`).

## 3D-pick model blobs (decoded 2026-08-07/10, one real 3D-pick backup)

`setting/<NNN>/T<xxx>/` folders on a 3D robot-pick program carry the vision
units' model data. Two container headers (both little-endian, verified
byte-exact: file = header + declared payload):

- `1001`-style (`.dat`, multi-section `.tbd`): u32 1001 · u32 1342 (header
  size) · u32 payload · u16 type id · cp932 label @0x0E · English label @0x4A
  (plus DE/FR/IT copies deeper in).
- `0x1c`-style (`TDC_L`/`WSM_L`/`LYT_G`): u32 28 · u32 1001 · … · u32 payload
  @0x10; payload holds zlib streams.

The zlib streams in `TDC_L` (part CAD) and `WSM_L` (workspace scan, or a part
copy in check tools) are **headerless binary-STL facet records** (50 B each).
`RBT_G_RMD_*.dat` names the cell's robot ("FANUC", "M-20iD/35") ahead of its
arm mesh - which uses the same uncompressed 48-byte records as `HND_L` (see
below): 32,250 facets, one fused mesh at the saved pose, not per-link parts.
`3D_RBT_G_CLB_*.dat` holds the hand-eye calibration run as 16-double pose-pair
records.

`HND_L` (gripper/EOAT) and the `RBT_G_RMD` arm decoded 2026-08-10: the same facet geometry, but
**uncompressed** and in **48-byte** records (twelve float32 — unit normal + 3
vertices, mm — with no u16 attribute word), starting at a file offset that is
**2 mod 4**. That misalignment is why an earlier pass, reading 4-aligned
floats, saw noise and called the encoding unreversed. Blocks are found by
self-validation (a run of records with unit-or-zero normals, grown outward),
never by a hardcoded offset; two real hand files decode to 46,952 and 481,039
facets. `TDM_L` (matching template) payload encoding remains undecoded.
Parser: `src/backupviewer/parsers/cvx_models.py`; viewer: the camera 3d view.

## `inspect.dat` — the inspection program container (2026-08-11)

One per program directory: `setting/<NNN>/inspect.dat`, where `<NNN>` is the
camera's own program number. Shape, measured over six real camera programs plus
a controlled experiment in the vendor's simulator:

- magic `ST` (2 bytes) — the only cheap gate; the *name* is never evidence.
- **98.2–98.7 % of the file is zlib**: 6–8 complete streams per file, inflating
  to ~0.2–2.4 MB each. They are the program's parameter memory — sparse, mostly
  zero, with two regions of self-describing text.
- The block set is written **twice**, a working copy and a recovery copy, the
  second at a fixed offset from the first. Every block but the leading one
  inflates byte-identically between the copies (the two leading blocks differ in
  bytes while matching in inflated size), so the reader folds duplicates on
  exact inflated content — never by position — and 6–8 streams become 3–5
  distinct contents.
- **Tool names** are a length-prefixed language table inside a block:

  ```
  (u32 language_index, u32 byte_length, <byte_length bytes>)  repeated
  ```

  indices strictly ascending, empty languages present at length 0, text cp932.
  A record carries about twenty consecutive slots (0–19) and has no length field
  of its own — the run ends where the bytes stop being plausible. **Slot 1 is
  English**, the slot a technician types and reads (the same slot
  `cvx_inspect.py` takes the program name from at 0x4C).
- **Calculation scripts** are plain ASCII in the CV-X's expression grammar
  (`@local` assignments, `ANSn` outputs, `IF`/`ELSEIF … THEN`/`ELSE`/`ENDIF`,
  `Tnnn.RSLT.<MNEMONIC>[i]:MS` cross-tool references, `'` comments).

Not decoded, and therefore not claimed anywhere: which **tool number** a name
record or a script belongs to, how to tell a technician's own name from the
vendor's built-in tool-type vocabulary, and the numeric parameter slots (float64
at an offset 6 mod 8, ~1e12 = unset). Parser:
`src/backupviewer/parsers/cvx_program.py`; surface: the camera logic tab; the
long form with evidence tags is in `docs/subsystems/parsing.md` §4.
