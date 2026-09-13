#!/usr/bin/env pwsh
<#
.SYNOPSIS
    One entry point for the common setup and diagnostic tasks.

.DESCRIPTION
    The individual scripts under tools/ each do one job well; this wraps them so
    a new machine does not require reading all of them first. Every action is a
    thin call into an existing script or the CLI -- no logic is duplicated here.

.EXAMPLE
    .\tools\eval.ps1 check
    Run the full health check: repository wiring, environment, and images.

.EXAMPLE
    .\tools\eval.ps1 build -UseMirror
    Build the local images (Codex agent, gateway, and optionally DSH).

.EXAMPLE
    .\tools\eval.ps1 setup
    Install Python and JavaScript dependencies.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("check", "doctor", "preflight", "setup", "build", "cache", "console", "test", "help")]
    [string]$Action = "check",

    # Which Task to check run-specific facts for (images, MCP hosts).
    [string]$Task,

    # Selection passed through to the CLI when -Task is given.
    [string]$Preset,
    [string]$Agent,
    [string]$Provider,
    [string]$Model,

    # Build options. `-Agent` above selects which Agents `build` builds; empty
    # builds every Agent whose profile declares how to build itself.
    [string]$Version,
    [switch]$UseMirror,
    [switch]$Offline,

    # Emit JSON instead of text where the underlying command supports it.
    [switch]$Json
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

function Write-Section([string]$Text) {
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Cyan
}

# PowerShell functions return everything written to the pipeline, not only the
# `return` value. Capturing a command's output into a variable would therefore
# also capture whatever the function returned, so the exit code travels through
# this script-scoped variable instead and the output streams straight through.
$script:LastExit = 0

function Invoke-Cli {
    param([string[]]$Arguments)
    # Array splat is correct here: the target is an external program, and each
    # element becomes one argv entry.
    & uv run ai-native-evals @Arguments
    $script:LastExit = $LASTEXITCODE
}

function Invoke-Script {
    # Named splat, not an array. `& $script @arguments` expands each element as a
    # *positional* argument, so `@("-Agent","codex")` bound "-Agent" to the first
    # parameter and left "codex" with nowhere to go. The build script then ran
    # with an empty distro name and every wsl call failed with
    # WSL_E_DISTRO_NOT_FOUND -- which is why `eval.ps1 build -Agent x` never
    # worked, while calling the build script directly did.
    param([string]$Path, [hashtable]$Named = @{})
    & $Path @Named
    $script:LastExit = $LASTEXITCODE
}

# `uv` and a synced environment are prerequisites for everything else, so they
# are reported before anything is attempted rather than failing inside a script.
function Test-Prerequisites {
    $problems = @()
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        $problems += "uv is not on PATH (https://docs.astral.sh/uv/)"
    }
    if (-not (Test-Path (Join-Path $RepoRoot ".venv"))) {
        $problems += "no .venv yet; run: .\tools\eval.ps1 setup"
    }
    return $problems
}

$selection = @()
if ($Preset) { $selection += @("--preset", $Preset) }
if ($Agent) { $selection += @("--agent", $Agent) }
if ($Provider) { $selection += @("--provider", $Provider) }
if ($Model) { $selection += @("--model", $Model) }

switch ($Action) {
    "help" {
        Get-Help $PSCommandPath -Detailed
        exit 0
    }

    "doctor" {
        Write-Section "Repository wiring"
        Invoke-Cli @("doctor")
        exit $script:LastExit
    }

    "preflight" {
        Write-Section "Machine environment"
        # Never `$args`: PowerShell's automatic argument array ignores
        # assignment, so what reached the child process was whatever this script
        # was invoked with rather than the switches built here.
        $cliArgs = @("preflight") + $selection
        if ($Task) { $cliArgs = @("preflight", $Task) + $selection }
        if ($Json) { $cliArgs += "--json" }
        Invoke-Cli $cliArgs
        exit $script:LastExit
    }

    "check" {
        # The two halves answer different questions and both are needed: a
        # correctly wired repository on a machine without Docker still cannot
        # run anything, and vice versa.
        $prereq = Test-Prerequisites
        if ($prereq.Count -gt 0) {
            Write-Section "Prerequisites"
            $prereq | ForEach-Object { Write-Host "  MISSING  $_" -ForegroundColor Red }
            Write-Host ""
            Write-Host "  Fix these first; the remaining checks need a synced environment." -ForegroundColor Yellow
            exit 1
        }

        Write-Section "Repository wiring"
        Invoke-Cli @("doctor")
        $doctorOk = $script:LastExit -eq 0

        Write-Section "Machine environment"
        $preflightArgs = @("preflight") + $selection
        if ($Task) { $preflightArgs = @("preflight", $Task) + $selection }
        Invoke-Cli $preflightArgs
        $preflightOk = $script:LastExit -eq 0

        Write-Section "Summary"
        $fmt = "  {0,-22} {1}"
        Write-Host ($fmt -f "repository wiring", $(if ($doctorOk) { "ok" } else { "PROBLEM" })) `
            -ForegroundColor $(if ($doctorOk) { "Green" } else { "Red" })
        Write-Host ($fmt -f "machine environment", $(if ($preflightOk) { "ok" } else { "NOT READY" })) `
            -ForegroundColor $(if ($preflightOk) { "Green" } else { "Red" })
        if ($doctorOk -and $preflightOk) {
            Write-Host ""
            Write-Host "  This machine can run an evaluation." -ForegroundColor Green
            exit 0
        }
        exit 1
    }

    "setup" {
        Write-Section "Python dependencies"
        & uv sync --dev
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        Write-Section "JavaScript dependencies (Eval Console)"
        if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
            Write-Host "  pnpm not found; the Console needs it, the evaluator does not." -ForegroundColor Yellow
            Write-Host "  Install with: corepack enable"
        }
        else {
            & pnpm install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  pnpm install failed; a lockfile change may need a plain 'pnpm install'." -ForegroundColor Yellow
            }
        }
        Write-Section "Done"
        Write-Host "  Next: .\tools\eval.ps1 check"
        exit 0
    }

    "build" {
        Write-Section "Building sandbox images"
        # Named splat: the build script's switches are parameters, not argv.
        $buildArgs = @{}
        if ($Agent) { $buildArgs["Agent"] = @($Agent) }
        if ($Version) { $buildArgs["Version"] = $Version }
        if ($UseMirror) { $buildArgs["UseMirror"] = $true }
        if ($Offline) { $buildArgs["Offline"] = $true }
        Invoke-Script (Join-Path $PSScriptRoot "build-sandbox-images.ps1") $buildArgs
        if ($script:LastExit -ne 0) { exit $script:LastExit }
        Write-Host ""
        Write-Host "  Verify with: .\tools\eval.ps1 check" -ForegroundColor Green
        exit 0
    }

    "cache" {
        Write-Section "Offline dependency cache"
        Invoke-Script (Join-Path $PSScriptRoot "prepare-offline-cache.ps1")
        if ($script:LastExit -ne 0) { exit $script:LastExit }
        Write-Section "Verifying cache"
        Invoke-Script (Join-Path $PSScriptRoot "verify-cache.ps1")
        exit $script:LastExit
    }

    "console" {
        Write-Section "AI-Native Eval Console"
        Invoke-Script (Join-Path $PSScriptRoot "console.ps1")
        exit $script:LastExit
    }

    "test" {
        Write-Section "Python tests"
        & uv run pytest -q
        $py = $LASTEXITCODE
        Write-Section "Lint"
        & uv run ruff check src tests
        $ruff = $LASTEXITCODE
        Write-Section "Console tests"
        $js = 0
        if (Get-Command pnpm -ErrorAction SilentlyContinue) {
            & pnpm test
            $js = $LASTEXITCODE
        }
        else {
            Write-Host "  skipped (pnpm not on PATH)" -ForegroundColor Yellow
        }
        Write-Section "Summary"
        Write-Host ("  python={0} ruff={1} console={2}" -f $py, $ruff, $js)
        exit ([int](($py -ne 0) -or ($ruff -ne 0) -or ($js -ne 0)))
    }
}
