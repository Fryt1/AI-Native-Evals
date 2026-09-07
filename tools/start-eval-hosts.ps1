[CmdletBinding()]
param(
    [string]$BlenderExecutable = "E:\blender\blender.exe",
    [string]$BlenderBootstrap = "D:\work\AI-Native-Game-Engine\artifacts\leopard2a4\mcp_bootstrap.py",
    [int]$BlenderPort = 9876,
    [string]$UnrealExecutable = "D:\UnrealEngine\ue5.8.2\UnrealEngine\Engine\Binaries\Win64\UnrealEditor.exe",
    [string]$UnrealProject = "D:\work\AI-Native\EvalRuns\ue5-host-fixture\ActorFixture.uproject",
    [int]$UnrealPort = 8000,
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CacheDir = Join-Path $RepoRoot "cache"
$FixtureSource = Join-Path $RepoRoot "fixtures\ue5\actor-fixture-mcp"
$FixtureRoot = Split-Path $UnrealProject -Parent

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
    $blender = Start-Process -FilePath $BlenderExecutable `
        -ArgumentList @("--background", "--python", $BlenderBootstrap) `
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
