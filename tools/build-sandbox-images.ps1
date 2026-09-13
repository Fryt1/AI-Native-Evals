[CmdletBinding()]
param(
    [string]$Distro = "Ubuntu-20.04",
    [switch]$UseMirror,
    [Alias("IncludeBlenderMcp")]
    [switch]$SkipMcp,
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
    # Use whichever PowerShell is running this script; `pwsh` is not on PATH
    # on a Windows PowerShell 5.1 host, and requiring it made the offline path
    # fail after it had already loaded the images.
    & (Get-Process -Id $PID).Path -NoProfile -File $verify -Distro $Distro -RequireDockerArchive
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

# One Codex image, and it bundles the MCP runtimes.
#
# Bundling is availability, not configuration: which MCP servers a run gets is
# decided entirely by its MCP profile, and the renderer never adds one on its
# own. A second, MCP-less variant only created a way for a task that needs
# Blender to run without it -- silently, because a missing stdio binary does
# not fail a run, it just removes tools the Agent was supposed to have.
$agentDockerfile = "$WslRoot/docker/codex-agent/Dockerfile"
$agentTag = "ai-native-codex-agent:local"
$agentArgs = @("build")
$agentArgs += $pullArgs
$agentArgs += $buildNetworkArgs
$agentArgs += @(
    "-f", $agentDockerfile,
    "--build-arg", "CODEX_VERSION=$CodexVersion",
    "--build-arg", "NODE_BASE_IMAGE=ai-native-llm-gateway:local",
    "-t", $agentTag
)

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
if ($SkipMcp) {
    Write-Warning "SkipMcp is deprecated: the single Codex image always bundles the MCP runtimes."
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
