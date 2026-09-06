param([switch]$Execute)
$ErrorActionPreference = 'Stop'
$log = 'D:\work\AI-Native\AI-Native-Evals\cache\eval-firewall.log'

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
  $exists = Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
  if ($exists) { Log "exists $Name"; return }
  try {
    New-NetFirewallRule -DisplayName $Name -Direction Inbound -Action Allow -Profile Any -Program $Program -Protocol TCP -LocalPort $Port -ErrorAction Stop | Out-Null
    Log "added $Name tcp/$Port"
  } catch { Log ("fail ${Name}: " + $_.Exception.Message) }
}

Add-EvalRule -Name 'AI-Native Eval UE5 MCP 8000' -Program 'D:\UnrealEngine\ue5.8.2\UnrealEngine\Engine\Binaries\Win64\UnrealEditor.exe' -Port 8000
Add-EvalRule -Name 'AI-Native Eval Blender MCP 9876' -Program 'E:\blender\blender.exe' -Port 9876
Add-EvalRule -Name 'AI-Native Eval ComfyUI 8188' -Program 'D:\work\Comfyui\ComfyUI-aki-v3.2\python\python.exe' -Port 8188
Log 'done'
