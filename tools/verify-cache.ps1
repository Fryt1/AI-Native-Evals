[CmdletBinding()]
param(
    [string]$Distro = "Ubuntu-20.04",
    [switch]$RequireDockerArchive,
    [switch]$CheckDockerImages
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CacheRoot = Join-Path $RepoRoot "cache"
$ManifestPath = Join-Path $CacheRoot "manifest.json"
$errors = [System.Collections.Generic.List[string]]::new()

function Require-File([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        $errors.Add("missing file: $Path")
    }
}

Require-File (Join-Path $CacheRoot "codex\openai-codex.tgz")
Require-File (Join-Path $CacheRoot "codex\codex-linux-x64.tgz")
Require-File (Join-Path $RepoRoot "vendor\blender-mcp\mcp.tar.gz")
Require-File (Join-Path $RepoRoot "vendor\blender-mcp\SOURCE.json")
Require-File (Join-Path $CacheRoot "python\blender-mcp\requirements.lock")
Require-File (Join-Path $CacheRoot "python\comfy-mcp\requirements.lock")

foreach ($name in @("blender-mcp", "comfy-mcp")) {
    $wheels = @(Get-ChildItem (Join-Path $CacheRoot "python\$name\wheels") -File -Filter "*.whl" -ErrorAction SilentlyContinue)
    if ($wheels.Count -eq 0) {
        $errors.Add("wheelhouse is empty: cache/python/$name/wheels")
    }
}

if ($RequireDockerArchive) {
    Require-File (Join-Path $CacheRoot "docker\sandbox-images.tar")
}

if (Test-Path $ManifestPath) {
    try {
        $manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
        if ($manifest.schema_version -ne 1) {
            $errors.Add("unsupported cache manifest schema: $($manifest.schema_version)")
        }
        foreach ($entry in @($manifest.files)) {
            $path = Join-Path $RepoRoot ($entry.path -replace '/', '\')
            Require-File $path
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($actual -ne $entry.sha256) {
                    $errors.Add("sha256 mismatch: $($entry.path) expected=$($entry.sha256) actual=$actual")
                }
            }
        }
    } catch {
        $errors.Add("could not validate cache manifest: $($_.Exception.Message)")
    }
} else {
    $errors.Add("missing cache manifest: $ManifestPath")
}

if ($CheckDockerImages) {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        $errors.Add("wsl.exe is not available")
    } else {
        foreach ($image in @("python:3.12-slim", "node:22-bookworm", "ai-native-llm-gateway:local", "ai-native-codex-agent:local", "ai-native-codex-agent:all-mcp")) {
            & wsl.exe -d $Distro -- docker image inspect $image *> $null
            if ($LASTEXITCODE -ne 0) {
                $errors.Add("Docker image is not loaded: $image")
            }
        }
    }
}

if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_ }
    exit 1
}

Write-Output "cache valid: $ManifestPath"
