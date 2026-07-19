# Keyence CV-X482D FTP layout (captured live 2026-07-13, .55)

- banner: `220 CV-X482D (6.0.0000) FTP server ready.`
- **anonymous FTP login works** (empty user/pass); lands at `/SD1/`
- `/SD1/cv-x/setting/` = config (env.dat, RBT_G_RMD/LYT `.dat`/`.tbd`, recovery/, numbered program dirs) -> backup target
- `/SD1/cv-x/box/` = saved sets: BOX_SD1_001_T100, BOX_SD1_001_T190, BOX_SD1_001_T101, BOX_SD1_001_T101_deep
- `/SD1/cv-x/temp/` = empty
- FTP quirk: `LIST <path>` returns 550; must CWD then bare LIST. Paths are relative; login dir = /SD1.
- setting/ file types: {'dat': 5, 'bmp': 2, 'tbd': 1}

=> Keyence backup is plain FTP (no Vapi.Net.dll needed): pull /SD1/cv-x/setting (+ optionally box). Mirrors mtxbackup.py.
