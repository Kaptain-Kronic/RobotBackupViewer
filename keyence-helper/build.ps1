<#
Builds KeyenceHelper.exe with the in-box .NET Framework compiler (no SDK /
MSBuild project needed - matches the "no build step" spirit of the Python
app's vanilla-JS frontend: one script, no project system to keep in sync).

Must target x86: Vapi.Net.dll is a 32-bit (C++/CLI mixed-mode) assembly and
will not load into a 64-bit process (confirmed: BadImageFormatException).

Vapi.Net.dll itself is NEVER copied into this repo or the output folder -
it's Keyence's proprietary interop library, already installed on this
machine alongside the CV-X Series Simulation-Software. We only reference it
by path to compile against its public API; VapiRuntime.EnsureResolvable()
finds the same install at run time via an AssemblyResolve hook.
#>
param(
    [string]$SdkDir = $env:KEYENCE_SDK_DIR
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $SdkDir) {
    $candidates = @(
        "C:\Program Files (x86)\KEYENCE\CV-X Series Terminal-Software\bin",
        "C:\Program Files (x86)\KEYENCE\CV-X Series Simulation-Software\bin_X400",
        "C:\Program Files (x86)\KEYENCE\CV-X Series Simulation-Software\bin_X200",
        "C:\Program Files (x86)\KEYENCE\CV-X Series Simulation-Software\bin_X100"
    )
    foreach ($c in $candidates) {
        if (Test-Path (Join-Path $c "Vapi.Net.dll")) { $SdkDir = $c; break }
    }
}
if (-not $SdkDir -or -not (Test-Path (Join-Path $SdkDir "Vapi.Net.dll"))) {
    throw "Could not find Vapi.Net.dll. Install the KEYENCE CV-X Series Simulation-Software, or pass -SdkDir <folder containing Vapi.Net.dll>."
}
Write-Host "Using Keyence SDK dir: $SdkDir"

$csc = "C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { throw "csc.exe not found at $csc" }

$outDir = Join-Path $root "bin"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$outExe = Join-Path $outDir "KeyenceHelper.exe"

$sources = Get-ChildItem -Path (Join-Path $root "src") -Filter "*.cs" | ForEach-Object { $_.FullName }

$vapiDll = Join-Path $SdkDir "Vapi.Net.dll"

& $csc /nologo /platform:x86 /target:exe /out:$outExe `
    "/reference:$vapiDll" `
    /reference:System.dll `
    /reference:System.Windows.Forms.dll `
    $sources

if ($LASTEXITCODE -ne 0) { throw "build failed" }

# Deploy the app.config next to the exe as KeyenceHelper.exe.config - it
# carries the runtime policy that lets native access violations be logged
# rather than silently killing the process.
$cfgSrc = Join-Path $root "KeyenceHelper.exe.config"
if (Test-Path $cfgSrc) {
    Copy-Item $cfgSrc (Join-Path $outDir "KeyenceHelper.exe.config") -Force
}

Write-Host "Built $outExe"
