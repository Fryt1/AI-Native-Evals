[CmdletBinding()]
param(
    [string]$Distro = "Ubuntu-20.04",
    [switch]$UseMirror,
    [Alias("IncludeAllMcp")]
    [switch]$IncludeBlenderMcp,
    [switch]$Offline,
    [switch]$IncludeDsh,
    [switch]$IncludeDshRelease,
    [string]$CodexVersion = "0.153.4",
    [string]$DshVersion = "0.1.2-rc.1",
    [string]$PythonBaseImage = "python:3.12-slim",
    [string]$NodeBaseImage = "node:22-bookworm",
    [string]$NpmRegistry = "https://registry.npmmirror.com",
    [string]$PyPIIndex = "https://pypi.tuna.tsinghua.edu.cn/simple"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Drive = $RepoRoot.Substring(0, 1).ToLowerInvariant()
$Relative = $RepoRoot.Substring(2).Replace("\", "/")
$WslRoot = "/mnt/$Drive$Relative"
$ArchiveWindows = Join-Path $RepoRoot "cache\docker\sandbox-images.tar"
$Archive = "$WslRoot/cache/docker/sandbox-images.tar"

function Invoke-Docker {
    param([string[]]$Arguments)
    & wsl.exe -d $Distro -- docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code $LASTEXITCODE"
    }
}

function Test-Image([string]$Image) {
    & wsl.exe -d $Distro -- docker image inspect $Image *> $null
    return $LASTEXITCODE -eq 0
}

if ($Offline) {
    $verify = Join-Path $RepoRoot "tools\verify-cache.ps1"
    if (-not (Test-Path -LiteralPath $ArchiveWindows -PathType Leaf)) {
        throw "Offline cache archive does not exist: $ArchiveWindows. Run prepare-offline-cache.ps1 first."
    }
    Write-Host "Loading cached Docker images..."
    Invoke-Docker @("load", "-i", $Archive)
    & pwsh -NoProfile -File $verify -Distro $Distro -RequireDockerArchive
    if ($LASTEXITCODE -ne 0) {
        throw "Offline cache verification failed"
    }
}

Write-Host "Ensuring local Codex package cache..."
$cacheVersion = Join-Path $RepoRoot "cache\codex\VERSION"
if (Test-Path $cacheVersion) {
    $CodexVersion = (Get-Content $cacheVersion -Raw).Trim()
}
& node (Join-Path $RepoRoot "tools\prepare-codex-cache.mjs") `
    --version $CodexVersion `
    --registry $NpmRegistry `
    --output-dir (Join-Path $RepoRoot "cache\codex")
if ($LASTEXITCODE -ne 0) {
    throw "Codex package cache preparation failed with exit code $LASTEXITCODE"
}

$buildNetworkArgs = if ($Offline) { @("--network", "none") } else { @() }
$pullArgs = @("--pull=false")

# Gateway is a tiny local image built from the cached Node base.
$gatewayArgs = @("build")
$gatewayArgs += $pullArgs
$gatewayArgs += $buildNetworkArgs
$gatewayArgs += @(
    "-f", "$WslRoot/gateway/Dockerfile",
    "--build-arg", "NODE_BASE_IMAGE=$NodeBaseImage",
    "-t", "ai-native-llm-gateway:local",
    $WslRoot
)
Write-Host "Building gateway image..."
Invoke-Docker $gatewayArgs

$agentDockerfile = if ($IncludeBlenderMcp) {
    "$WslRoot/docker/codex-agent/Dockerfile"
} else {
    "$WslRoot/docker/codex-agent/Dockerfile.sandbox"
}
$agentTag = if ($IncludeBlenderMcp) {
    "ai-native-codex-agent:all-mcp"
} else {
    "ai-native-codex-agent:local"
}
$agentArgs = @("build")
$agentArgs += $pullArgs
$agentArgs += $buildNetworkArgs
$agentArgs += @(
    "-f", $agentDockerfile,
    "--build-arg", "CODEX_VERSION=$CodexVersion",
    "--build-arg", "NODE_BASE_IMAGE=ai-native-llm-gateway:local",
    "-t", $agentTag
)

if ($IncludeBlenderMcp) {
    if ($UseMirror -and -not $Offline) {
        $PythonBaseImage = "mirror.gcr.io/library/python:3.12-slim"
    }
    $agentArgs += @(
        "--build-arg", "PYTHON_BASE_IMAGE=$PythonBaseImage",
        "--build-arg", "PYPI_INDEX_URL=$PyPIIndex"
    )
    if ($Offline) {
        $agentArgs += @("--build-arg", "OFFLINE=1")
    }
} elseif ($Offline) {
    # Dockerfile.sandbox installs Codex from the local npm tarballs only.
    $agentArgs += @("--build-arg", "NPM_REGISTRY=$NpmRegistry")
}
$agentArgs += $WslRoot

Write-Host "Building Codex sandbox image ($agentTag)..."
Invoke-Docker $agentArgs

if ($IncludeDsh) {
    $DshRootWindows = (Resolve-Path (Join-Path $RepoRoot "..\dsh")).Path
    $DshDrive = $DshRootWindows.Substring(0, 1).ToLowerInvariant()
    $DshRelative = $DshRootWindows.Substring(2).Replace("\", "/")
    $DshRoot = "/mnt/$DshDrive$DshRelative"
    $DshCommit = (& git -C $DshRootWindows rev-parse --verify HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($DshCommit)) {
        throw "Could not resolve the DSH repository commit: $DshRootWindows"
    }
    $dshDockerIgnore = Join-Path $DshRootWindows ".dockerignore"
    $createdDshDockerIgnore = $false
    try {
        if (-not (Test-Path -LiteralPath $dshDockerIgnore -PathType Leaf)) {
            $ignoreLines = @(
                ".git", ".github", "node_modules", "website", "snapshots", "docs",
                "**/tests", "**/test", "**/*.spec.ts", "**/*.test.ts",
                "**/coverage", "**/dist", "**/lib"
            )
            $ignoreLines | Set-Content -LiteralPath $dshDockerIgnore -Encoding utf8
            $createdDshDockerIgnore = $true
        }
        $dshArgs = @(
            "build", "--pull=false",
            "-f", "$WslRoot/docker/dsh-agent/Dockerfile",
            "--build-arg", "NODE_BASE_IMAGE=$NodeBaseImage",
            "--build-arg", "NPM_REGISTRY=$NpmRegistry",
            "--build-arg", "DSH_COMMIT=$DshCommit",
            "-t", "ai-native-dsh-agent:local",
            $DshRoot
        )
        Write-Host "Building DSH ACP sandbox image (commit $DshCommit)..."
        Invoke-Docker $dshArgs
    }
    finally {
        if ($createdDshDockerIgnore) {
            Remove-Item -LiteralPath $dshDockerIgnore -Force
        }
    }
}

if ($IncludeDshRelease) {
    $dshReleaseArgs = @(
        "build", "--pull=false",
        "-f", "$WslRoot/docker/dsh-agent/Dockerfile.release",
        "--build-arg", "NODE_BASE_IMAGE=$NodeBaseImage",
        "--build-arg", "NPM_REGISTRY=$NpmRegistry",
        "--build-arg", "DSH_VERSION=$DshVersion",
        "-t", "ai-native-dsh-agent:release",
        $WslRoot
    )
    Write-Host "Building DSH release ACP sandbox image..."
    Invoke-Docker $dshReleaseArgs
}

Write-Host "Sandbox images are ready."
