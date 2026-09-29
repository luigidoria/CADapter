# V1 acceptance battery (no Ollama), in three phases kept apart:
#   checks      deterministic, no SOLIDWORKS (catalog, scenarios, comparator, boundaries)
#   version     the cross-version battery (93 scenarios) -> one JSON report
#   regression  the seven live families, ONE family at a time against ONE SOLIDWORKS
#   compare     that report against the other version's committed baseline
# Everything it produces goes to a results folder OUTSIDE the repository, plus a .zip of
# that folder to carry to the other machine.
#
# Usage, from any directory (SOLIDWORKS open, unrelated work saved, no other CAD client):
#   powershell -NoProfile -ExecutionPolicy Bypass -File tests\run_v1_battery.ps1
#   ... -Out D:\somewhere           a NEW or EMPTY directory (default:
#                                   %USERPROFILE%\cadapter_runs\<stamp>-sw<rev>). A
#                                   non-empty one, or an existing <Out>.zip, is refused:
#                                   nothing is deleted, and <Out>\work can never hold CAD
#                                   output from an earlier run
#   ... -ExpectedRevision 25.3.0    refuse to start unless the SOLIDWORKS that answers
#                                   has this RevisionNumber ("25" = any 25.x.y, i.e.
#                                   SW 2017; "34" = SW 2026). Only verifies; it never
#                                   launches or switches SOLIDWORKS
#   ... -Skip version,regression    (phases: checks, version, regression, compare);
#                                   a skipped phase is reported SKIPPED BY REQUEST
#   ... -CloseAll                   DESTRUCTIVE: CloseAllDocuments(True) before the battery
#                                   and between families -- unsaved work in ANY open
#                                   document is lost
#   ... -AllowRestart               DESTRUCTIVE: a hung family force-kills EVERY
#                                   SLDWORKS.exe and relaunches it (tests\run_regression.py)
#
# Default (isolated) mode: every test closes only the documents it opened, and all CAD
# output goes to <Out>\work -- nothing is written into the repository except the
# report that `-Skip` leaves out. Documents you had open stay open; for reference-grade
# evidence, start from an EMPTY SOLIDWORKS session anyway.
#
# Exit code: 0 when every phase that ran passed AND the evidence .zip was written; 1
# when any phase failed (the phase and the reason are at the end of SUMMARY.txt),
# including a requested comparison with no report or no other baseline, or a failed
# .zip; 2 when it could not start (no SOLIDWORKS, wrong -ExpectedRevision, -Out not
# empty). PASS/FAIL/ERROR/SKIP/KNOWN/NOT RUN are defined in tests\release_gate.py: an
# accepted [KNOWN ...] or optional [SKIP] is listed in SUMMARY.txt, never counted as a
# pass. Written in ASCII on purpose: Windows PowerShell 5.1 reads a BOM-less file with
# the ANSI code page.
param(
    [string]$Out = "",
    [string]$ExpectedRevision = "",
    [string[]]$Skip = @(),
    [switch]$CloseAll,
    [switch]$AllowRestart
)
$ErrorActionPreference = "Continue"
$Skip = @($Skip | ForEach-Object { $_ -split "," } | Where-Object { $_ })

# Shared mode is the development workflow: the shared tests\validation_assembly tree that
# development probes read, close-all between families and kill/relaunch recovery.
$Shared = $false
if (@("1", "true", "yes", "on") -contains "$env:CADAPTER_TEST_ISOLATED".ToLower()) { $Shared = $false }
elseif (@("0", "false", "no", "off") -contains "$env:CADAPTER_TEST_ISOLATED".ToLower()) { $Shared = $true }
if ($Shared) {
    $CloseAll = $true
    $AllowRestart = $true
    $env:CADAPTER_TEST_ISOLATED = "0"
} else {
    $env:CADAPTER_TEST_ISOLATED = "1"
}

$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "No venv at $py. From the repo root:"
    Write-Host "  py -3.12 -m venv .venv; .venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 2
}
Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"

# attach only: this script never launches (or relaunches) SOLIDWORKS by itself
$rev = (& $py -c "from solidworks import sw_com as C; print(C.connect(launch=False).RevisionNumber())" 2>$null | Select-Object -Last 1)
if (-not $rev) { Write-Host "SOLIDWORKS did not answer over COM -- open it and run again."; exit 2 }
$rev = "$rev".Trim()
if ($ExpectedRevision) {
    # before anything is written or run: on a machine with several SOLIDWORKS versions,
    # the one that answers COM is not necessarily the one the operator meant to test
    & $py tests\release_gate.py revision $ExpectedRevision $rev
    if ($LASTEXITCODE -ne 0) { exit 2 }
}
$tag = "sw" + ($rev -replace "\.", "_")
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
if (-not $Out) { $Out = Join-Path $env:USERPROFILE "cadapter_runs\$stamp-$tag" }
# acceptance evidence comes from THIS run only: a non-empty -Out (or its .zip) is refused,
# never cleaned -- the families reuse a saved part or assembly when the file exists
& $py tests\release_gate.py fresh-out $Out
if ($LASTEXITCODE -ne 0) { exit 2 }
if (Test-Path "$Out.zip") { Write-Host "Evidence archive already exists: $Out.zip. Use a new directory for release validation."; exit 2 }
New-Item -ItemType Directory -Force $Out | Out-Null
$Out = (Resolve-Path $Out).Path
if ($Shared) {
    $regDir = "tests\validation_assembly\regression"
} else {
    # ONE output root for every family of the run: the drawing family reuses the clevis
    # built earlier IN THIS RUN, never one left over from another run
    $env:CADAPTER_TEST_OUT = Join-Path $Out "work"
    $regDir = Join-Path $env:CADAPTER_TEST_OUT "regression"
}
$failed = New-Object System.Collections.ArrayList
$skipped = New-Object System.Collections.ArrayList
foreach ($phase in "checks", "version", "regression", "compare") {
    if ($Skip -contains $phase) { [void]$skipped.Add($phase) }
}

function Log([string]$line) {
    $l = "[$(Get-Date -Format s)] $line"
    Write-Host $l
    Add-Content -Path "$Out\progress.log" -Value $l -Encoding UTF8
}

function Close-All {
    if (-not $CloseAll) { return }
    & $py -c "from solidworks import sw_com as C; C.connect(launch=False); a = C.app(); n = a.GetDocumentCount(); a.CloseAllDocuments(True); print('closed', n, 'left', a.GetDocumentCount())" 2>&1 |
        ForEach-Object { "$_" } | Add-Content -Path "$Out\close_all.log" -Encoding UTF8
}

function Run-Py([string]$name, [string[]]$pyargs) {
    Log "START $name :: $($pyargs -join ' ')"
    & $py -u @pyargs 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath "$Out\$name.log" -Encoding UTF8
    $code = $LASTEXITCODE
    Log "END   $name exit=$code"
    if ($code -ne 0) { [void]$failed.Add("$name (exit $code)") }
}

# identity of what was measured: without it a result cannot be compared later
git rev-parse HEAD | Out-File "$Out\git_head.txt" -Encoding UTF8
git status --short | Out-File "$Out\git_status.txt" -Encoding UTF8
git diff HEAD | Out-File "$Out\git_diff.patch" -Encoding UTF8
& $py --version 2>&1 | ForEach-Object { "$_" } | Out-File "$Out\python.txt" -Encoding UTF8
"SOLIDWORKS $rev" | Out-File "$Out\solidworks.txt" -Encoding UTF8
Log "SOLIDWORKS $rev | HEAD $(git rev-parse --short HEAD) | results -> $Out"
if ($Shared) { Log "mode: SHARED (tests\validation_assembly, close-all, kill/relaunch recovery)" }
else { Log "mode: ISOLATED (CAD output in $env:CADAPTER_TEST_OUT, only test documents closed)" }
if ($CloseAll) { Log "!! -CloseAll: every open document is closed WITHOUT saving before the battery and between families" }
if ($AllowRestart) { Log "!! -AllowRestart: a hang force-kills EVERY SLDWORKS.exe and relaunches it" }

if ($Skip -notcontains "checks") {
    Run-Py "check_catalog" @("solidworks\tests\unit\check_catalog.py")
    Run-Py "check_component_refs" @("solidworks\tests\unit\check_component_refs.py")
    Run-Py "check_scenarios" @("tests\version\check_scenarios.py")
    Run-Py "check_compare" @("tests\version\check_compare.py")
    Run-Py "check_modularity" @("-m", "unittest", "tests.test_modularity")
    Run-Py "check_knowledge" @("mcp_server\tests\test_knowledge.py")
    Run-Py "check_release_gate" @("-m", "unittest", "tests.test_release_gate")
}

$report = "$Out\suite_version_$tag.json"
if ($Skip -notcontains "version") {
    Close-All
    # --baseline keeps the machine name OUT of the report, so it can be versioned as
    # tests\version\reports\BASELINE_<tag>.json after the comparison, as it is
    Run-Py "suite_version" @("tests\version\suite_version.py", "--baseline", "--out", $report)
}

if ($env:CADAPTER_DRAWINGS_SKIP) { Log "drawings: skipping case(s) $env:CADAPTER_DRAWINGS_SKIP on this machine" }
if ($Skip -notcontains "regression") {
    $restart = @()
    if ($AllowRestart) { $restart = @("--allow-restart") }
    foreach ($fam in "part", "assembly", "clevis", "drawing", "sheetmetal", "benchmark", "drawings") {
        Close-All
        Remove-Item "$regDir\summary.json" -ErrorAction SilentlyContinue
        Run-Py "regression_$fam" (@("tests\run_regression.py", $fam) + $restart)
        Copy-Item "$regDir\summary.json" "$Out\regression_$fam.summary.json" -ErrorAction SilentlyContinue
    }
}
Close-All

if ($Skip -notcontains "compare") {
    # this run's report against every OTHER committed baseline (release_gate.compare_phase).
    # A requested comparison that cannot happen -- no report from this run (e.g. -Skip
    # version without -Skip compare), no other baseline -- FAILS; a nonzero comparator is
    # a REGRESSION/GEOMETRY/OLD_BUG finding and fails too.
    Run-Py "compare" @("tests\release_gate.py", "compare", "--report", $report, "--rev", $rev, "--out", $Out)
} else {
    Log "compare: SKIPPED BY REQUEST (-Skip compare)"
}

# the verdict lines, in one file
$summary = @("SOLIDWORKS $rev | HEAD $(git rev-parse --short HEAD) | $stamp", "")
foreach ($f in Get-ChildItem "$Out\*.log" | Sort-Object Name) {
    $hits = Select-String -Path $f.FullName -Pattern "^\S.*\s(PASS|FAIL|HUNG|ERROR|INCOMPLETE|NOT RUN)\s+\d+/\d+", "CRASH", "SUMMARY:", "steps, \d+ with an ERROR", "^VERDICT:", "^families:", "^compare", "^\s*(OK|FAILED)\b", "all ok", "Ran \d+ tests", "every step maps", "^RESUMO:", "^(REFERENCE|NEW) ", "\[SKIP\]", "\[KNOWN" |
        Select-Object -First 16 | ForEach-Object { "    " + $_.Line.Trim() }
    $summary += $f.BaseName
    $summary += $hits
}
$summary += ""
foreach ($phase in $skipped) { $summary += "PHASE ${phase}: SKIPPED BY REQUEST" }

function Write-Summary {
    $verdict = if ($failed.Count) { "VERDICT: FAIL -- " + ($failed -join "; ") }
               else { "VERDICT: PASS (every phase that ran; accepted [SKIP]/[KNOWN] lines are listed above, never counted as passes)" }
    ($summary + $verdict) | Out-File "$Out\SUMMARY.txt" -Encoding UTF8
}
Write-Summary

$zip = "$Out.zip"
# the evidence, not the CAD output: <Out>\work stays next to the zip for inspection.
# The zip is part of the acceptance: without it the run has no evidence to carry.
try {
    Compress-Archive -Path (Get-ChildItem $Out -Exclude "work" | ForEach-Object { $_.FullName }) -DestinationPath $zip -ErrorAction Stop
    if (-not (Test-Path $zip)) { throw "Compress-Archive reported success but wrote no file" }
    Log "evidence -> $zip"
} catch {
    Log "evidence zip FAILED: $($_.Exception.Message)"
    Remove-Item $zip -ErrorAction SilentlyContinue   # a partial archive is not evidence
    [void]$failed.Add("evidence zip ($($_.Exception.Message))")
    Write-Summary
}
Get-Content "$Out\SUMMARY.txt"
Log "ALL DONE -> $Out"
if ($failed.Count) { exit 1 }
exit 0
