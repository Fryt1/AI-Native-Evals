param([switch]$Execute)
$ErrorActionPreference = 'Stop'
# Derived from this script's own location, so the repository can live anywhere.
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$log = Join-Path $repo 'cache\eval-firewall.log'

function Log($m) { Add-Content -LiteralPath $log -Value ("{0} {1}" -f (Get-Date -Format o), $m) }

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $Execute) {
  if (-not $isAdmin) {
    try {
      Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File', $MyInvocation.MyCommand.Path, '-Execute'
      ) | Out-Null
    } catch {
      Log ("elevation-failed: " + $_.Exception.Message)
      exit 1
    }
  } else {
    Log 'already-admin'
  }
  exit 0
}

if (-not $isAdmin) { Log 'still-not-admin'; exit 1 }
Log 'elevated-start'

function Add-EvalRule {
  param([string]$Name,[string]$Program,[int]$Port)
  if (-not $Program) {
    # Say so instead of creating a rule with an empty program, which would
    # silently allow nothing (or worse, match unintended traffic).
    Log "skipped $Name (tcp/$Port): no program configured"
    return
  }
  $exists = Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
  if ($exists) { Log "exists $Name"; return }
  try {
    New-NetFirewallRule -DisplayName $Name -Direction Inbound -Action Allow -Profile Any -Program $Program -Protocol TCP -LocalPort $Port -ErrorAction Stop | Out-Null
    Log "added $Name tcp/$Port"
  } catch { Log ("fail ${Name}: " + $_.Exception.Message) }
}

# Host application paths are local deployment details with no portable default.
# Supply the ones you use through these environment variables.
Add-EvalRule -Name 'AI-Native Eval UE5 MCP 8000' -Program $env:AI_NATIVE_EVALS_UNREAL_EXE -Port 8000
Add-EvalRule -Name 'AI-Native Eval Blender MCP 9876' -Program $env:AI_NATIVE_EVALS_BLENDER_EXE -Port 9876
Add-EvalRule -Name 'AI-Native Eval ComfyUI 8188' -Program $env:AI_NATIVE_EVALS_COMFY_PYTHON -Port 8188
Log 'done'
