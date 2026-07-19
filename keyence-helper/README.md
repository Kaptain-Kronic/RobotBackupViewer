# keyence-helper

A tiny x86 .NET Framework console tool that wraps KEYENCE's **`Vapi.Net.dll`**
(the vendor's "Vision API" interop library) so the Python backup app can
**discover** CV-X vision controllers on the network and **back up** their
settings — the same two things the shop does today by hand, one camera at a
time, in the KEYENCE Terminal-Software.

It is the camera-side analogue of `src/backupviewer/ftpbackup.py` +
`discover.py`: FANUC robots speak FTP (Python talks to them directly);
KEYENCE cameras speak a proprietary protocol that only lives inside
`Vapi.Net.dll`, so we shell out to this helper and parse its JSON output.

## Why a separate C# process

`Vapi.Net.dll` is a **32-bit, mixed-mode (C++/CLI) .NET Framework 4.5.1**
assembly. It can't be loaded into a 64-bit process (`BadImageFormatException`)
and isn't callable from Python without a CLR host. A small `[STAThread]`
console exe is the clean seam: it does the interop, prints one JSON object per
line to stdout, and the Python job wrapper reads that stream exactly like it
already reads FTP progress.

**We never copy `Vapi.Net.dll` into this repo.** It's KEYENCE's proprietary,
per-machine install. The helper finds it at run time (see search order below);
`build.ps1` references it by path at compile time.

## Build

```powershell
powershell -File keyence-helper\build.ps1
# -> keyence-helper\bin\KeyenceHelper.exe
```

No SDK or `.csproj` needed — it compiles with the in-box .NET Framework
`csc.exe` (`C:\Windows\Microsoft.NET\Framework\v4.0.30319`), matching the
"no build step" spirit of the app's vanilla-JS frontend. `/platform:x86` is
mandatory (the vendor DLL is 32-bit).

### Where the vendor DLL is found (compile time and run time)

1. `%KEYENCE_SDK_DIR%` (override — point at any folder holding `Vapi.Net.dll`)
2. `…\CV-X Series Terminal-Software\bin` ← **preferred**: the build the shop
   runs against live cameras, guaranteed installed on a backup PC
3. `…\CV-X Series Simulation-Software\bin_X400` / `bin_X200` / `bin_X100`
   (fallbacks — a dev box that only has the simulator)

The Terminal and Simulation builds ship **identical** `Keyence.Ve.Interop`
types and signatures (verified), so either compiles/runs; the Terminal one is
just the hardware-proven default.

## Commands

```
KeyenceHelper.exe discover [--addr=<ip>] [--timeout=ms]
KeyenceHelper.exe diagnose <ip> [--timeout=ms]     # ZERO writes
KeyenceHelper.exe backup <ip> <destDir> [--root=SD1:\cv-x\setting] [--timeout=ms]
```

Every line of stdout is one JSON object with a `"type"` field
(`start` / `found` / `progress` / `error` / `done` / …). `discover` emits a
`found` line per controller; `backup` emits `progress` lines shaped like
`ftpbackup.BackupJob`'s snapshot (`current` / `done` / `total` / `bytes`).

## What's confirmed vs. still open

Confirmed by reflecting the DLL, reading the Terminal-Software config, and
running the helper on a PC with the SDK but **no camera**:

- ✅ The interop is structurally correct: builds clean, loads the vendor DLL,
  and reaches KEYENCE's native engine — a malformed detect address returns
  `INVALID_PARAM`, a well-formed-but-unreachable one returns `IO_ERROR`
  (i.e. "nobody answered"), which is exactly right with no camera present.
- ✅ **Drive / path syntax is `SD1:\` and `SD2:\`** (from the Terminal config:
  `DEFAULT_CONTROLLER_FILE_GET_DIRECTORY = SD1:\`, `DRIVE1=SD1`, `DRIVE2=SD2`).
  A real backup places settings under `SD1:\cv-x\setting` — the default
  `backup` scope, matching the shop's existing tool.
- ✅ No Administrator needed — the Terminal exe runs `asInvoker`.

Open — **needs one run against a live CV-X** (the reason `diagnose` exists;
it's read-only, same role `discover.py:diagnose_controller` played for tuning
the FANUC side against a live R-30iB):

- ❓ **`discover --addr` semantics.** `findControllerEther(timeout, addr,
  FIND_MODEL_CVX)` rejects `""` (`INVALID_PARAM`). Unclear yet whether `addr`
  should be this PC's NIC IP (broadcast *from* that subnet), the camera's IP
  (targeted probe), or a subnet broadcast address. Try the PC's own adapter IP
  first, then the camera's known IP.
- ❓ **Connect-by-IP.** `CameraSession.Open` builds a `VapiCommSystemInfo`
  (address + `ETHERNET`) and calls `assignSystemId` → `createSession` →
  `connect`. Best reading of the API, but inferred — if `diagnose` fails at
  the connect step, start here.
- ❓ **Exact backup subtree / whole-card question.** Default grabs
  `SD1:\cv-x\setting` (what the current tool captures). `diagnose` prints the
  real `SD1:\` tree so we can decide whether a full backup should take the
  whole card (logs/captures included) or just the settings.
- ❓ **Directory attribute bit.** The recursive lister skips dirs via a
  DOS-style `0x10` attribute bit (`RemoteFileWalker.ATTR_DIRECTORY`) — confirm
  against real `fileAttribute` values in `diagnose` output.

### How to run the live test

On the shop network, with a CV-X reachable:

```powershell
# 1. find it (try your PC's NIC IP, then the camera's IP)
.\bin\KeyenceHelper.exe discover --addr=<this-PC-ip> --timeout=3000
.\bin\KeyenceHelper.exe discover --addr=<camera-ip>  --timeout=3000

# 2. read-only probe of the on-card tree (safe on a running camera)
.\bin\KeyenceHelper.exe diagnose <camera-ip>

# 3. once diagnose looks right, a real pull
.\bin\KeyenceHelper.exe backup <camera-ip> C:\temp\cam-test
```

Send back the JSON from steps 1–2 (errors included) — that's what pins down
the open questions above, after which the Python side (`keyence_backup.py`, a
`BackupJob`-shaped wrapper around this exe) is mechanical.
