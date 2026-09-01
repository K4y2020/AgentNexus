#!/usr/bin/env pwsh

# Candidate-release soak driver for the AgentNexus control plane (Windows).
#
# Repeatedly runs the mock Plan -> Implement -> Review -> Test reliability
# baseline for a fixed wall-clock window, then compares a process baseline
# taken before the first iteration against each post-iteration snapshot to
# flag orphaned runner/harness/server processes from the sampled runs. A
# failed sample, a non-zero pytest exit, or any newly-persisting sampled-run
# process fails the soak.
#
# The script is safe to run with a normal local server/host up: baseline
# captures processes that already exist, and the leak fingerprint only matches
# the per-user pytest temp workspace used by the sampled runs.
#
# Usage:
#   .\scripts\run_control_plane_soak.ps1 -DurationMinutes 1440 -SamplesPerIteration 10
#   $env:RELIABILITY_ARTIFACTS=".reliability-results"; .\scripts\run_control_plane_soak.ps1 -DurationMinutes 30
#
# Requires PowerShell 7+ and uv. The deterministic mock LLM keeps the loop
# credential-free; vendor CLI binaries are not needed.

param(
    [int]$DurationMinutes = 1440,
    [int]$SamplesPerIteration = 10,
    [string]$ArtifactDir = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = git -C $PSScriptRoot rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0) {
    throw "Could not locate the AgentNexus repository root via git."
}

if ($DurationMinutes -lt 1) {
    throw "DurationMinutes must be a positive integer; got '$DurationMinutes'"
}
if ($SamplesPerIteration -lt 1) {
    throw "SamplesPerIteration must be a positive integer; got '$SamplesPerIteration'"
}

if (-not $ArtifactDir) {
    $ArtifactDir = $env:RELIABILITY_ARTIFACTS
}
if (-not $ArtifactDir) {
    $ArtifactDir = Join-Path $repoRoot ".reliability-results"
}
$soakDir = Join-Path $ArtifactDir "soak"
New-Item -ItemType Directory -Force -Path $soakDir | Out-Null

# Fingerprint of the sampled-run process tree: the pytest command line, its
# env var, or the ephemeral per-user temp workspace it boots servers from.
$procPattern = "test_control_plane_reliability_runs\.py|AGENTNEXUS_RELIABILITY_RUNS|pytest-of-"

function Get-CandidatePids {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match $procPattern } |
        ForEach-Object { [int]$_.ProcessId }
}

$baseline = @(Get-CandidatePids | Sort-Object -Unique)
$start = Get-Date
$deadline = $start.AddMinutes($DurationMinutes)
$iterationRows = [System.Collections.Generic.List[object]]::new()
$samplesRun = 0
$failed = $false

Write-Host "Soak window: $DurationMinutes minute(s), $SamplesPerIteration sample(s) per iteration."
Write-Host "Artifacts: $soakDir"
Write-Host ("Baseline sampled-run processes: {0}" -f $baseline.Count)

Push-Location $repoRoot
try {
    $iteration = 0
    while ((Get-Date) -lt $deadline) {
        $iteration++
        $iterStart = Get-Date
        $env:AGENTNEXUS_RELIABILITY_RUNS = [string]$SamplesPerIteration
        $junitXml = Join-Path $soakDir ("soak-iter-{0:D3}.xml" -f $iteration)
        $iterLog = Join-Path $soakDir ("soak-iter-{0:D3}.log" -f $iteration)
        Write-Host ("[{0}] iteration {1}: running {2} samples..." -f $iterStart.ToString("HH:mm:ss"), $iteration, $SamplesPerIteration)
        & uv run --no-sync pytest -q `
            tests/integration/test_control_plane_reliability_runs.py `
            -p no:cacheprovider `
            --maxfail=1 `
            "--junitxml=$junitXml" 2>&1 | Tee-Object -FilePath $iterLog
        $iterExit = $LASTEXITCODE
        if ($iterExit -ne 0) {
            $failed = $true
        }

        # Allow teardown to finish before looking for leftovers.
        Start-Sleep -Seconds 20
        $current = @(Get-CandidatePids | Sort-Object -Unique)
        $leakPids = @($current | Where-Object { $baseline -notcontains $_ })

        if ($leakPids.Count -gt 0) {
            $failed = $true
            Write-Host ("LEAK: {0} process(es) from the sampled run survived teardown: {1}" -f $leakPids.Count, ($leakPids -join ", "))
        }

        $samplesRun += $SamplesPerIteration
        $iterationRows.Add([pscustomobject]@{
            iteration = $iteration
            samples = $SamplesPerIteration
            exit_code = $iterExit
            leak_pids = $leakPids
            started_at = $iterStart
            finished_at = Get-Date
        })

        if ($failed) {
            break
        }
    }
}
finally {
    Pop-Location
}

$elapsed = [math]::Round(((Get-Date) - $start).TotalMinutes, 2)
$allLeakPids = @($iterationRows | ForEach-Object { $_.leak_pids } | Sort-Object -Unique)

$summary = [pscustomobject]@{
    script = "run_control_plane_soak.ps1"
    started_at = $start
    finished_at = Get-Date
    duration_minutes = $elapsed
    configured_minutes = $DurationMinutes
    samples_per_iteration = $SamplesPerIteration
    iterations = $iterationRows.Count
    samples_run = $samplesRun
    failed = $failed
    leak_pids = $allLeakPids
    iteration_rows = $iterationRows
}
$summaryPath = Join-Path $soakDir "summary.json"
$summary | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 -Path $summaryPath

Write-Host ("Soak summary: {0} iterations, {1} samples, duration {2} min, failed={3}, leaks={4}" -f `
    $iterationRows.Count, $samplesRun, $elapsed, $failed, $allLeakPids.Count)
Write-Host "Summary: $summaryPath"

if ($failed) {
    throw "Soak failed; inspect $soakDir"
}
Write-Host "OK: soak window completed without sample failures or process leaks."
