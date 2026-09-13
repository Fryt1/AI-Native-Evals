[CmdletBinding()]
param(
    [int]$Port = 8787,
    # Not `$Host`: that name is a read-only automatic variable in PowerShell, so
    # declaring it as a parameter makes the script fail before it does anything.
    [string]$BindAddress = "127.0.0.1",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
Push-Location $repo
try {
    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
        throw "pnpm is required to build the Console frontend."
    }
    if (-not $SkipBuild) {
        # Workspace root commands. `apps/eval-console` is a workspace member via
        # pnpm-workspace.yaml; the old per-directory install is what a fresh
        # clone silently skipped.
        pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        pnpm build
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    uv run ai-native-evals console --host $BindAddress --port $Port
} finally {
    Pop-Location
}
