[CmdletBinding()]
param(
    # Every path below is a local deployment detail and has no safe machine-independent
    # default. Supply them explicitly, or set the matching environment variable.
    [string]$BlenderExecutable = $env:AI_NATIVE_EVALS_BLENDER_EXE,
    [string]$BlenderBootstrap = $env:AI_NATIVE_EVALS_BLENDER_BOOTSTRAP,
    [int]$BlenderPort = 9876,
    [string]$UnrealExecutable = $env:AI_NATIVE_EVALS_UNREAL_EXE,
    [string]$UnrealProject = $env:AI_NATIVE_EVALS_UNREAL_PROJECT,
    [int]$UnrealPort = 8000,
    # Blender resolves a leading `/workspace` against its own working directory,
    # so where this process starts decides where an Agent's `/workspace/...`
    # saves actually land. Point it at the directory the runs' workspaces live
    # under, or a Task that saves through MCP will write somewhere the run
    # cannot read.
    [string]$HostWorkspaceDirectory = $env:AI_NATIVE_EVALS_HOST_WORKSPACE,
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CacheDir = Join-Path $RepoRoot "cache"
$FixtureSource = Join-Path $RepoRoot "fixtures\ue5\actor-fixture-mcp"

# The UE5 fixture project is created by this script when none was supplied, so a
# blank value is valid rather than an error.
$FixtureRoot = if ($UnrealProject) { Split-Path $UnrealProject -Parent } else { Join-Path $RepoRoot "..\EvalRuns\ue5-host-fixture" }
if (-not $UnrealProject) {
    $UnrealProject = Join-Path $FixtureRoot "ActorFixture.uproject"
}

if ($BlenderBootstrap -and -not (Test-Path -LiteralPath $BlenderBootstrap)) {
    throw "BlenderBootstrap does not exist: $BlenderBootstrap`nSet -BlenderBootstrap or `$env:AI_NATIVE_EVALS_BLENDER_BOOTSTRAP."
}
if ($UnrealExecutable -and -not (Test-Path -LiteralPath $UnrealExecutable)) {
    throw "UnrealExecutable does not exist: $UnrealExecutable`nSet -UnrealExecutable or `$env:AI_NATIVE_EVALS_UNREAL_EXE."
}
if ($BlenderExecutable -and -not (Test-Path -LiteralPath $BlenderExecutable)) {
    throw "BlenderExecutable does not exist: $BlenderExecutable`nSet -BlenderExecutable or `$env:AI_NATIVE_EVALS_BLENDER_EXE."
}

function Test-Listening([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-Listening([int]$Port, [string]$Name) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Listening $Port) {
            Write-Host "$Name is listening on port $Port"
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "$Name did not listen on port $Port within $TimeoutSeconds seconds"
}

if (-not (Test-Path -LiteralPath $BlenderExecutable -PathType Leaf)) {
    throw "Blender executable does not exist: $BlenderExecutable"
}
if (-not (Test-Path -LiteralPath $UnrealExecutable -PathType Leaf)) {
    throw "UnrealEditor executable does not exist: $UnrealExecutable"
}
if (-not (Test-Path -LiteralPath $UnrealProject -PathType Leaf)) {
    if (-not (Test-Path -LiteralPath $FixtureRoot)) {
        New-Item -ItemType Directory -Force -Path $FixtureRoot | Out-Null
        Copy-Item -LiteralPath $FixtureSource -Destination $FixtureRoot -Recurse -Force
    } else {
        throw "UE project does not exist and target directory is not empty: $UnrealProject"
    }
}

# UE's HTTPServer module defaults to localhost. The WSL Docker network reaches
# the Windows host through its interface address, so the eval-only fixture
# explicitly listens on any address. This does not change user projects.
$ueConfig = Join-Path $FixtureRoot "Config\DefaultEngine.ini"
if (-not (Test-Path -LiteralPath $ueConfig)) {
    throw "UE fixture config does not exist: $ueConfig"
}
$ueText = Get-Content -LiteralPath $ueConfig -Raw
if ($ueText -notmatch '(?m)^\[HTTPServer\.Listeners\]') {
    Add-Content -LiteralPath $ueConfig -Value "`n[HTTPServer.Listeners]`nDefaultBindAddress=any`n"
} elseif ($ueText -notmatch '(?m)^DefaultBindAddress=') {
    Add-Content -LiteralPath $ueConfig -Value "DefaultBindAddress=any`n"
}

# Blender bridge reads these variables from mcp_bootstrap.py.
$env:BLENDER_MCP_BIND_HOST = "0.0.0.0"
$env:BLENDER_MCP_BIND_PORT = "$BlenderPort"

$blenderLog = Join-Path $CacheDir "blender-bridge.log"
$blenderErr = Join-Path $CacheDir "blender-bridge.err.log"
$ueLog = Join-Path $CacheDir "ue5-mcp-run.log"
$ueErr = Join-Path $CacheDir "ue5-mcp-run.err.log"

if (-not (Test-Listening $BlenderPort)) {
    # `--command blender_mcp` is how Blender 5.x starts the bundled MCP server.
    # Three things about it are easy to get wrong and each cost a debugging
    # round: `--background` must precede `--command`; `--factory-startup` must
    # NOT be used, because it disables the user-installed addon that provides
    # the command; and online access has to be granted or Blender refuses to
    # start it at all.
    #
    # `--host 0.0.0.0` matters as much as the port: the addon defaults to
    # localhost, which the container cannot reach.
    $blenderArguments = @(
        "--background",
        "--online-mode",
        "--command", "blender_mcp",
        "--host", "0.0.0.0",
        "--port", "$BlenderPort"
    )
    if ($BlenderBootstrap) {
        # A bootstrap script is still honoured, for deployments that start the
        # bridge their own way; it runs as an extra Python step before startup.
        $blenderArguments = @("--background", "--python", $BlenderBootstrap) +
            $blenderArguments[1..($blenderArguments.Count - 1)]
    }
    $blender = Start-Process -FilePath $BlenderExecutable `
        -ArgumentList $blenderArguments `
        -WorkingDirectory $HostWorkspaceDirectory `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $blenderLog -RedirectStandardError $blenderErr
    Set-Content -LiteralPath (Join-Path $CacheDir "blender-bridge.pid") -Value $blender.Id
    Write-Host "Started Blender MCP bridge pid=$($blender.Id)"
} else {
    Write-Host "Blender MCP port $BlenderPort is already listening"
}

if (-not (Test-Listening $UnrealPort)) {
    $ue = Start-Process -FilePath $UnrealExecutable `
        -ArgumentList @($UnrealProject, "-ModelContextProtocolStartServer", "-ModelContextProtocolPort=$UnrealPort", "-unattended", "-nop4", "-nosplash") `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $ueLog -RedirectStandardError $ueErr
    Set-Content -LiteralPath (Join-Path $CacheDir "ue5-mcp-run.pid") -Value $ue.Id
    Write-Host "Started UE5 MCP pid=$($ue.Id)"
} else {
    Write-Host "UE5 MCP port $UnrealPort is already listening"
}

Wait-Listening $BlenderPort "Blender MCP"
Wait-Listening $UnrealPort "UE5 MCP"
Write-Host "Evaluation host services are ready."
