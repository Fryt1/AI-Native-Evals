[CmdletBinding()]
param(
    [int[]]$Ports = @(9876, 8000)
)

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CacheDir = Join-Path $RepoRoot "cache"

foreach ($port in $Ports) {
    $connections = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    foreach ($connection in $connections) {
        $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
        if ($process) {
            $command = (Get-CimInstance Win32_Process -Filter "ProcessId=$($process.Id)" -ErrorAction SilentlyContinue).CommandLine
            if ($command -match "UnrealEditor|blender.exe" -and ($command -match "ModelContextProtocol|mcp_bootstrap")) {
                Write-Host "Stopping eval host $($process.ProcessName) pid=$($process.Id) port=$port"
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            } else {
                Write-Warning "Refusing to stop unrelated process pid=$($process.Id) on port $port"
            }
        }
    }
}
Write-Host "Evaluation host services stopped."
