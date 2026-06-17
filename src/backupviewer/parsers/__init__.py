"""FANUC backup file parsers.

All parsers are pure functions text -> JSON-serializable dicts/lists.
TAB_REQUIREMENTS maps each UI tab to the backup files that make it available
(a tab lights up when ANY of its files is present; special "*"-prefixed
entries are handled by BackupSession).
"""

TAB_REQUIREMENTS = {
    "overview": ["SUMMARY.DG"],
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
    "sysvars": ["SYSTEM.VA"],
    "mhvalves": ["MHGRIPDT.VA"],
    "files": [],
}
