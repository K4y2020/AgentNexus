#!/usr/bin/env pwsh

# Repeatable control-plane reliability baseline for native Windows/PowerShell.
#
# Runs `AGENTNEXUS_RELIABILITY_RUNS` independent Plan -> Implement -> Review ->
# Test workflows against the real local server, runner, and wrapped SDK harness
# using the deterministic mock LLM. Each sampled run gets a fresh session tree
# and terminal-idle receipt; a single infrastructure failure fails the sample.
#
# Usage:
#   $env:RUNS=100; .\scripts\run_control_plane_reliability.ps1
#   $env:RUNS=5; $env:RELIABILITY_ARTIFACTS=".reliability-results"; .\scripts\run_control_plane_reliability.ps1
#   .\scripts\run_control_plane_reliability.ps1 -Runs 3
#
# Requires PowerShell 7+, uv, and the test/integration dependencies. Mock mode
# does not need vendor CLI binaries; it exercises the wrapped openai-agents SDK
# harness against the deterministic mock LLM.

param(
    [int]$Runs = 0,
    [string]$ArtifactDir = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = git -C $PSScriptRoot rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0) {
    throw "Could not locate the AgentNexus repository root via git."
}

if ($Runs -lt 1) {
    $envRuns = $env:RUNS
    if (-not $envRuns) {
        $envRuns = $env:AGENTNEXUS_RELIABILITY_RUNS
    }
    if (-not $envRuns) {
        $envRuns = "100"
    }
    $Runs = [int]$envRuns
}

if ($Runs -lt 1) {
    throw "RUNS must be a positive integer; got '$Runs'"
}

if (-not $ArtifactDir) {
    $ArtifactDir = $env:RELIABILITY_ARTIFACTS
}
if (-not $ArtifactDir) {
    $ArtifactDir = Join-Path $repoRoot ".reliability-results"
}

New-Item -ItemType Directory -Force -Path $ArtifactDir | Out-Null
$junitXml = Join-Path $ArtifactDir "reliability.xml"
$env:AGENTNEXUS_RELIABILITY_RUNS = [string]$Runs

Write-Host "Running $Runs independent control-plane workflow samples..."
Write-Host "Artifacts: $ArtifactDir"

Push-Location $repoRoot
try {
    & uv run --no-sync pytest -q `
        tests/integration/test_control_plane_reliability_runs.py `
        -p no:cacheprovider `
        --maxfail=1 `
        "--junitxml=$junitXml"
    if ($LASTEXITCODE -ne 0) {
        throw "pytest exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Write-Host "OK: $Runs control-plane reliability runs completed."
