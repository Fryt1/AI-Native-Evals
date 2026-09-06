[CmdletBinding()]
param(
    [string]$Distro = "Ubuntu-20.04",
    [switch]$UseMirror,
    [switch]$IncludeBlenderMcp,
    [string]$CodexVersion = "0.153.4",
    [string]$NpmRegistry = "https://registry.npmmirror.com"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Drive = $RepoRoot.Substring(0, 1).ToLowerInvariant()
$Relative = $RepoRoot.Substring(2).Replace("\", "/")
$WslRoot = "/mnt/$Drive$Relative"

function Invoke-Docker {
    param([string[]]$Arguments)
    & wsl.exe -d $Distro -- docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code $LASTEXITCODE"
    }
}

$GatewayArgs = @(
    "build",
    "-f", "$WslRoot/gateway/Dockerfile",
    "-t", "ai-native-llm-gateway:local"
)
$AgentDockerfile = if ($IncludeBlenderMcp) {
    "$WslRoot/docker/codex-agent/Dockerfile"
} else {
    "$WslRoot/docker/codex-agent/Dockerfile.sandbox"
}
$AgentTag = if ($IncludeBlenderMcp) {
    "ai-native-codex-agent:all-mcp"
} else {
    "ai-native-codex-agent:local"
}
$AgentArgs = @(
    "build",
    "-f", $AgentDockerfile,
    "--build-arg", "CODEX_VERSION=$CodexVersion",
    "--build-arg", "NODE_BASE_IMAGE=ai-native-llm-gateway:local",
    "-t", $AgentTag
)

$CacheScript = Join-Path $RepoRoot "tools/prepare-codex-cache.mjs"
$CacheDir = Join-Path $RepoRoot "cache/codex"
Write-Host "Ensuring local Codex package cache..."
& node $CacheScript --version $CodexVersion --registry $NpmRegistry --output-dir $CacheDir
if ($LASTEXITCODE -ne 0) {
    throw "Codex package cache preparation failed with exit code $LASTEXITCODE"
}

$gatewayExists = $false
& wsl.exe -d $Distro -- docker image inspect ai-native-llm-gateway:local *> $null
if ($LASTEXITCODE -eq 0) {
    $gatewayExists = $true
}

if (-not $gatewayExists) {
    if ($UseMirror) {
        $Mirror = "mirror.gcr.io/library"
        $GatewayArgs += @("--build-arg", "NODE_BASE_IMAGE=$Mirror/node:22-slim")
    }
    Write-Host "Building gateway image..."
    Invoke-Docker ($GatewayArgs + $WslRoot)
} else {
    Write-Host "Gateway image already exists; reusing it."
}

if ($UseMirror) {
    $AgentArgs += @("--build-arg", "NPM_REGISTRY=$NpmRegistry")
    if ($IncludeBlenderMcp) {
        $Mirror = "mirror.gcr.io/library"
        $AgentArgs += "--build-arg", "PYTHON_BASE_IMAGE=$Mirror/python:3.12-slim"
    }
}

Write-Host "Building Codex sandbox image ($AgentTag)..."
Invoke-Docker ($AgentArgs + $WslRoot)
Write-Host "Sandbox images are ready."
