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
`RBT_G_RMD_*.dat` names the cell's robot ("FANUC", "M-20iD/35") ahead of an
unreversed mesh; `3D_RBT_G_CLB_*.dat` holds the hand-eye calibration run as
16-double pose-pair records; `HND_L`/`TDM_L` payload encodings remain undecoded.
Parser: `src/backupviewer/parsers/cvx_models.py`; viewer: the camera 3d view.
