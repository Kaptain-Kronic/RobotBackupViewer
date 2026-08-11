"""FANUC backup file parsers.

All parsers are pure functions text -> JSON-serializable dicts/lists.
TAB_REQUIREMENTS maps each UI tab to the backup files that make it available
(a tab lights up when ANY of its files is present; special "*"-prefixed
entries are handled by BackupSession).
"""

TAB_REQUIREMENTS = {
    # a camera backup has no SUMMARY.DG but earns its own overview
    # ("*camera" = any camera backup type; BackupSession special-cases it)
    "overview": ["SUMMARY.DG", "*camera"],
    # payloads fold into the frames tab, so frames lights up on either file
    "frames": ["SYSFRAME.VA", "SYMOTN.VA"],
    "io": ["IOCONFIG.DG", "IOSTATE.DG", "SUMMARY.DG"],
    "registers": ["NUMREG.VA", "POSREG.VA", "STRREG.VA"],
    "programs": ["*programs"],
    "alarms": ["*alarms"],
    # macros are no longer their own tab - this flag drives the "macros" button
    # inside the programs tab (manifest.tabs.macros)
    "macros": ["SUMMARY.DG", "SYSMACRO.VA"],
    "dcs": ["DCSVRFY.DG", "DCSCHGD1.DG", "DCSDIFF.DG"],
    # the 3D zone view draws from DCSPOS.VA (authoritative geometry) but can
    # fall back to the verify report's diagonal boxes; on a Keyence camera it
    # instead shows the backup's 3D model blobs ("*cvx3d" = vouched CV-X model
    # containers present - special-cased by BackupSession)
    "view3d": ["DCSPOS.VA", "DCSVRFY.DG", "*cvx3d"],
    # a keyence camera's inspection programs: tool names + calculation logic
    "logic": ["*cvxlogic"],
    "sysvars": ["SYSTEM.VA"],
    "mhvalves": ["MHGRIPDT.VA"],
    # matrox camera: lights up when the backup carries saved inspection photos
    # (BackupSession special-cases "*photos", like "*programs"/"*alarms")
    "photos": ["*photos"],
    "files": [],
}
