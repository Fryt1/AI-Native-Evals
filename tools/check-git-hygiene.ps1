<#
.SYNOPSIS
Fails when something that must not live in Git has been tracked.

.DESCRIPTION
.gitignore stops accidental `git add .`, but it cannot stop `git add -f` or a
stale index carried over from another checkout. This check inspects what Git is
actually tracking and fails on:

  * paths that must never be committed (Docker image archives, virtualenvs,
    node_modules, build output, per-run workspaces);
  * any tracked file larger than -MaxTrackedKB.

Run it before pushing, and after any bulk `git add`.

.PARAMETER MaxTrackedKB
Largest tracked file allowed, in KB. Default 5120 (5 MB), which leaves room for
the one intentionally vendored archive, vendor/blender-mcp/mcp.tar.gz (~2.9 MB).

.EXAMPLE
pwsh -File tools/check-git-hygiene.ps1
#>
[CmdletBinding()]
param(
    [int]$MaxTrackedKB = 5120
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repo
try {
    $tracked = @(git ls-files)
    if ($LASTEXITCODE -ne 0) { throw "git ls-files failed" }

    $problems = [System.Collections.Generic.List[string]]::new()

    # Paths that must never appear in the index, however they got there.
    $forbidden = [ordered]@{
        "offline cache: Docker image archive" = "^cache/docker/"
        "offline cache: agent tarballs"       = "^cache/codex/.+\.tgz$"
        "offline cache: wheelhouse"           = "^cache/python/.+/wheels/.+\.whl$"
        "virtualenv"                          = "^\.venv/"
        "node_modules"                        = "node_modules/"
        "python build output"                 = "^dist/"
        "per-run workspace"                   = "^runs/"
        "inspect transcripts"                 = "^logs/"
        "run index"                           = "^EvalRuns/"
        "generated Console copy"              = "^src/ai_native_evals_console/static/"
        "process id file"                     = "\.pid$"
        # .env, .env.local, ... but NOT the committed .env.example template.
        "env file"                            = "(^|/)\.env(\.(?!example).*)?$"
    }
    foreach ($entry in $forbidden.GetEnumerator()) {
        foreach ($hit in ($tracked | Where-Object { $_ -match $entry.Value })) {
            $problems.Add("$($entry.Key) is tracked: $hit")
        }
    }

    # Size ceiling: catches a large binary that no path rule anticipated.
    foreach ($rel in $tracked) {
        $full = Join-Path $repo $rel
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { continue }
        $kb = (Get-Item -LiteralPath $full).Length / 1KB
        if ($kb -gt $MaxTrackedKB) {
            $problems.Add(("tracked file exceeds {0} KB: {1} ({2:N0} KB)" -f $MaxTrackedKB, $rel, $kb))
        }
    }

    if ($problems.Count -gt 0) {
        Write-Host "Git hygiene check FAILED ($($problems.Count) problem(s)):" -ForegroundColor Red
        foreach ($problem in $problems) { Write-Host "  - $problem" -ForegroundColor Red }
        Write-Host ""
        Write-Host "If a file was force-added, drop it from the index with:" -ForegroundColor Yellow
        Write-Host "  git rm --cached <path>" -ForegroundColor Yellow
        exit 1
    }

    Write-Host ("Git hygiene OK: {0} tracked files, largest under {1} KB." -f $tracked.Count, $MaxTrackedKB) -ForegroundColor Green
}
finally {
    Pop-Location
}
