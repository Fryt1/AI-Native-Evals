[CmdletBinding()]
param(
    [string]$Distro = "Ubuntu-20.04",
    # Which Agents to build, by profile id. Empty builds every Agent whose
    # profile declares a `build`; an Agent that declares none has nothing to
    # build and is reported as such.
    [string[]]$Agent = @(),
    # Override the version being built, for Agents named in -Agent. Ignored when
    # several Agents are built at once, because one version cannot describe them
    # all: name the Agent it belongs to.
    [string]$Version = "",
    [switch]$UseMirror,
    [Alias("IncludeBlenderMcp")]
    [switch]$SkipMcp,
    [switch]$Offline,
    [switch]$SkipGateway,
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

<#
Read every Agent profile that declares how to build itself.

The profiles are read through the same loader the evaluator uses, so this script
cannot disagree with a run about what a profile means. It used to hold its own
list -- `-CodexVersion`, `-DshVersion`, `-IncludeDshRelease` and a hard-coded
Dockerfile path per Agent -- which meant adding an Agent required editing shared
code, and an Agent whose packaging changed needed the script changed with it.
#>
function Get-AgentBuilds {
    # The loader runs from a file rather than `python -c`, because PowerShell
    # strips the quotes out of an inline program: a double-quoted argument loses
    # the `"` characters Python needs, and the script reported every profile as
    # unreadable.
    $loaderPath = Join-Path ([System.IO.Path]::GetTempPath()) "ai-native-agent-builds.py"
    $loader = @'
import json, sys
from pathlib import Path
sys.path.insert(0, "src")
from ai_native_evals.agents.profile import load_agent_profiles

out = []
for profile_id, profile in sorted(load_agent_profiles(Path("profiles/agents")).items()):
    out.append({
        "id": profile_id,
        "dockerfile": str(profile.build.get("dockerfile") or ""),
        "version_arg": str(profile.build.get("version_arg") or ""),
        "repository": profile.image_repository,
        "version": profile.agent_version,
        "image": profile.image,
        "build_args": {str(k): str(v) for k, v in (profile.build.get("args") or {}).items()},
    })
print(json.dumps(out))
'@
    Set-Content -LiteralPath $loaderPath -Value $loader -Encoding utf8
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }
    Push-Location $RepoRoot
    try {
        $json = & $python $loaderPath
    }
    finally {
        Pop-Location
        Remove-Item -LiteralPath $loaderPath -Force -ErrorAction SilentlyContinue
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read Agent profiles from profiles/agents"
    }
    return $json | ConvertFrom-Json
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

$builds = Get-AgentBuilds
if ($Agent.Count -gt 0) {
    $known = $builds | ForEach-Object { $_.id }
    $unknown = $Agent | Where-Object { $_ -notin $known }
    if ($unknown) {
        throw "Unknown Agent(s): $($unknown -join ', '). Known: $($known -join ', ')"
    }
    $builds = $builds | Where-Object { $_.id -in $Agent }
}
if ($Version -and ($builds | Measure-Object).Count -ne 1) {
    throw "-Version names one Agent's version; pair it with a single -Agent."
}

$buildNetworkArgs = if ($Offline) { @("--network", "none") } else { @() }
$pullArgs = @("--pull=false")

# Large build inputs are fetched outside Docker so an offline build has them on
# disk; that is what lets `-Offline` pass `--network none` and still work. Which
# inputs a given Agent needs is visible in its Dockerfile -- it COPYs them -- so
# this reads the Dockerfile rather than keeping a list of Agents.
function Initialize-BuildInputs {
    param([string]$Dockerfile, [string]$Version)
    $dockerfilePath = Join-Path $RepoRoot $Dockerfile
    if (-not (Test-Path -LiteralPath $dockerfilePath -PathType Leaf)) { return }
    $body = Get-Content -LiteralPath $dockerfilePath -Raw
    if ($body -notmatch 'cache/codex/') { return }
    Write-Host "Ensuring local Codex package cache..."
    $cacheVersion = Join-Path $RepoRoot "cache\codex\VERSION"
    $effective = $Version
    if (-not $effective -and (Test-Path -LiteralPath $cacheVersion)) {
        # A requested version wins over the recorded one. Reading the cache
        # unconditionally meant `-Version 0.154.0` built 0.153.4 and reported
        # success, so a second version could never be produced.
        $effective = (Get-Content $cacheVersion -Raw).Trim()
    }
    if (-not $effective) { return }
    & node (Join-Path $RepoRoot "tools\prepare-codex-cache.mjs") `
        --version $effective `
        --registry $NpmRegistry `
        --output-dir (Join-Path $RepoRoot "cache\codex")
    if ($LASTEXITCODE -ne 0) {
        throw "Codex package cache preparation failed with exit code $LASTEXITCODE"
    }
    # Plain Set-Content, so PowerShell applies the line-ending convention the
    # working tree uses. A byte-exact write through .NET left the tracked file
    # modified after every build under `core.autocrlf=true`.
    $effective | Set-Content -LiteralPath $cacheVersion
}

# Gateway is a tiny local image built from the cached Node base. The Codex image
# uses it as its Node base, so it comes first.
if (-not $SkipGateway) {
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
}

$built = @()
$skipped = @()
foreach ($build in $builds) {
    if (-not $build.dockerfile) {
        $skipped += "$($build.id) (nothing to build: no dockerfile declared)"
        continue
    }
    $version = if ($Version) { $Version } else { $build.version }
    if (-not $version) {
        throw "Agent $($build.id) has no version and none was given with -Version"
    }
    $tag = if ($build.repository) { "$($build.repository):$version" } else { $build.image }
    if (-not $tag) {
        throw "Agent $($build.id) has no image repository; add image_repository to its profile"
    }
    $dockerfile = "$WslRoot/$($build.dockerfile)"
    Initialize-BuildInputs -Dockerfile $build.dockerfile -Version $version
    $args = @("build")
    $args += $pullArgs
    $args += $buildNetworkArgs
    $args += @("-f", $dockerfile)
    if ($build.version_arg) {
        $args += @("--build-arg", "$($build.version_arg)=$version")
    }
    if ($UseMirror -and -not $Offline) {
        $PythonBaseImage = "mirror.gcr.io/library/python:3.12-slim"
    }
    $args += @(
        "--build-arg", "PYTHON_BASE_IMAGE=$PythonBaseImage",
        "--build-arg", "PYPI_INDEX_URL=$PyPIIndex",
        "--build-arg", "NPM_REGISTRY=$NpmRegistry"
    )
    if ($Offline) {
        $args += @("--build-arg", "OFFLINE=1")
    }
    if ($SkipMcp) {
        Write-Warning "SkipMcp is deprecated: the Agent image always bundles the MCP runtimes."
    }
    # Extra build arguments the profile declares. A Dockerfile's ARG defaults are
    # its own business, and passing one Agent's value to every Agent broke another:
    # `NODE_BASE_IMAGE` was set to the gateway image for all of them, so a
    # Dockerfile expecting the plain Node base ran `npm install -g` as the
    # unprivileged `node` user and failed with EACCES.
    foreach ($key in ($build.build_args.PSObject.Properties.Name)) {
        $args += @("--build-arg", "$key=$($build.build_args.$key)")
    }
    $args += @("-t", $tag, $WslRoot)
    Write-Host "Building Agent $($build.id) as $tag..."
    Invoke-Docker $args
    $built += $tag
}

Write-Host ""
if ($built.Count -gt 0) {
    Write-Host "Built $($built.Count) image(s):"
    $built | ForEach-Object { Write-Host "  $_" }
}
if ($skipped.Count -gt 0) {
    Write-Host "Nothing to build for $($skipped.Count) Agent(s):"
    $skipped | ForEach-Object { Write-Host "  $_" }
}
if ($built.Count -eq 0 -and $skipped.Count -eq 0) {
    throw "No Agent profiles found under profiles/agents"
}
Write-Host "Sandbox images are ready."
