[CmdletBinding()]
param(
    [string]$Distro = "Ubuntu-20.04",
    [string]$PythonBaseImage = "python:3.12-slim",
    [string]$NodeBaseImage = "node:22-bookworm",
    [string]$PyPIIndex = "https://pypi.tuna.tsinghua.edu.cn/simple",
    [string]$NpmRegistry = "https://registry.npmmirror.com",
    [switch]$RegenerateLocks,
    [switch]$RefreshWheels,
    [switch]$SkipImageSave
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Drive = $RepoRoot.Substring(0, 1).ToLowerInvariant()
$Relative = $RepoRoot.Substring(2).Replace("\", "/")
$WslRoot = "/mnt/$Drive$Relative"
$CacheRoot = Join-Path $RepoRoot "cache"
$DockerCache = Join-Path $CacheRoot "docker"
$PythonCache = Join-Path $CacheRoot "python"
$ImageArchive = Join-Path $DockerCache "sandbox-images.tar"
$ManifestPath = Join-Path $CacheRoot "manifest.json"

function Invoke-WslDocker {
    param([string[]]$Arguments)
    & wsl.exe -d $Distro -- docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code $LASTEXITCODE"
    }
}

function Invoke-WslDockerOutput {
    param([string[]]$Arguments)
    $result = & wsl.exe -d $Distro -- docker @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw (($result | Out-String).Trim())
    }
    return ($result | Out-String).Trim()
}

function Ensure-Dir([string]$Path) {
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

Ensure-Dir $DockerCache
Ensure-Dir (Join-Path $PythonCache "blender-mcp\wheels")
Ensure-Dir (Join-Path $PythonCache "comfy-mcp\wheels")

Write-Host "Preparing Codex package cache..."
& node (Join-Path $RepoRoot "tools\prepare-codex-cache.mjs") `
    --version (Get-Content (Join-Path $CacheRoot "codex\VERSION") -Raw).Trim() `
    --registry $NpmRegistry `
    --output-dir (Join-Path $CacheRoot "codex")
if ($LASTEXITCODE -ne 0) {
    throw "Codex cache preparation failed with exit code $LASTEXITCODE"
}

Write-Host "Ensuring base Docker images are local..."
foreach ($image in @($PythonBaseImage, $NodeBaseImage)) {
    & wsl.exe -d $Distro -- docker image inspect $image *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Pulling $image..."
        Invoke-WslDocker @("pull", $image)
    } else {
        Write-Host "Using local $image"
    }
}

# A lock is generated from a known-good all-mcp image when requested. The
# Blender local source package itself is intentionally not put in the PyPI
# wheelhouse; it is installed from vendor/blender-mcp/mcp.tar.gz.
$allMcpExists = $false
& wsl.exe -d $Distro -- docker image inspect ai-native-codex-agent:all-mcp *> $null
$allMcpExists = $LASTEXITCODE -eq 0
if ($RegenerateLocks) {
    if (-not $allMcpExists) {
        throw "-RegenerateLocks requires ai-native-codex-agent:all-mcp to exist"
    }
    Write-Host "Regenerating Python locks from ai-native-codex-agent:all-mcp..."
    $blenderLock = Join-Path $PythonCache "blender-mcp\requirements.lock"
    $comfyLock = Join-Path $PythonCache "comfy-mcp\requirements.lock"
    wsl.exe -d $Distro -- docker run --rm --entrypoint /bin/sh ai-native-codex-agent:all-mcp `
        -c "/opt/blender-mcp/bin/pip freeze" 2>&1 |
        Where-Object { $_ -notmatch '^blender-mcp @ ' } |
        Set-Content -Path $blenderLock -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Could not regenerate Blender lock" }
    wsl.exe -d $Distro -- docker run --rm --entrypoint /bin/sh ai-native-codex-agent:all-mcp `
        -c "/opt/comfy-mcp/bin/pip freeze" 2>&1 |
        Set-Content -Path $comfyLock -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Could not regenerate Comfy lock" }
}

foreach ($lock in @(
    (Join-Path $PythonCache "blender-mcp\requirements.lock"),
    (Join-Path $PythonCache "comfy-mcp\requirements.lock")
)) {
    if (-not (Test-Path $lock)) {
        throw "Missing lock file: $lock. Run with -RegenerateLocks after building all-mcp."
    }
}

function Download-Wheels([string]$Name) {
    $base = Join-Path $PythonCache $Name
    $lock = Join-Path $base "requirements.lock"
    $wheels = Join-Path $base "wheels"
    $existing = @(Get-ChildItem $wheels -File -Filter "*.whl" -ErrorAction SilentlyContinue)
    if ($existing.Count -gt 0 -and -not $RefreshWheels) {
        Write-Host "Using existing $Name wheelhouse ($($existing.Count) wheels)"
        return
    }
    Write-Host "Downloading $Name wheelhouse..."
    $wslLock = "$WslRoot/cache/python/$Name/requirements.lock"
    $wslWheels = "$WslRoot/cache/python/$Name/wheels"
    Invoke-WslDocker @(
        "run", "--rm",
        "--mount", "type=bind,src=$WslRoot,dst=/cache",
        $PythonBaseImage,
        "sh", "-lc",
        "pip download --no-cache-dir --only-binary=:all: --requirement /cache/cache/python/$Name/requirements.lock --dest /cache/cache/python/$Name/wheels --index-url $PyPIIndex"
    )
}

Download-Wheels "blender-mcp"
Download-Wheels "comfy-mcp"

if (-not $SkipImageSave) {
    $images = @(
        $PythonBaseImage,
        $NodeBaseImage,
        "ai-native-llm-gateway:local",
        "ai-native-codex-agent:local",
        "ai-native-codex-agent:all-mcp"
    )
    $available = @()
    foreach ($image in $images) {
        & wsl.exe -d $Distro -- docker image inspect $image *> $null
        if ($LASTEXITCODE -eq 0) {
            $available += $image
        } else {
            Write-Warning "Image not local; it will not be saved: $image"
        }
    }
    if ($available.Count -gt 0) {
        Write-Host "Saving local image bundle to $ImageArchive..."
        $saveArgs = @("save", "-o", "$WslRoot/cache/docker/sandbox-images.tar")
        $saveArgs += $available
        Invoke-WslDocker $saveArgs
    }
}

function Get-ImageRecord([string]$Image) {
    $raw = Invoke-WslDockerOutput @("image", "inspect", $Image)
    $obj = $raw | ConvertFrom-Json
    [PSCustomObject]@{
        name = $Image
        id = $obj.Id
        repo_digests = @($obj.RepoDigests)
        created = $obj.Created
        size_bytes = $obj.Size
    }
}

$records = @()
foreach ($image in @($PythonBaseImage, $NodeBaseImage, "ai-native-llm-gateway:local", "ai-native-codex-agent:local", "ai-native-codex-agent:all-mcp")) {
    & wsl.exe -d $Distro -- docker image inspect $image *> $null
    if ($LASTEXITCODE -eq 0) { $records += Get-ImageRecord $image }
}

$inputFiles = @()
$inputFiles += Get-ChildItem (Join-Path $CacheRoot "codex") -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @("VERSION", "openai-codex.tgz", "codex-linux-x64.tgz") }
$inputFiles += Get-ChildItem (Join-Path $CacheRoot "python") -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in @(".lock", ".whl") }
if (Test-Path $ImageArchive) {
    $inputFiles += Get-Item -LiteralPath $ImageArchive
}
$inputFiles += Get-Item -LiteralPath (Join-Path $RepoRoot "vendor\blender-mcp\mcp.tar.gz")
$inputFiles += Get-Item -LiteralPath (Join-Path $RepoRoot "vendor\blender-mcp\SOURCE.json")

$files = @()
foreach ($file in $inputFiles | Sort-Object FullName -Unique) {
    $files += [PSCustomObject]@{
        path = $file.FullName.Substring($RepoRoot.Length + 1).Replace("\", "/")
        size_bytes = $file.Length
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

$gitCommit = (& git -C $RepoRoot rev-parse HEAD 2>$null).Trim()
$manifest = [ordered]@{
    schema_version = 1
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    platform = "linux/amd64"
    repository_commit = $gitCommit
    registries = [ordered]@{
        pypi = $PyPIIndex
        npm = $NpmRegistry
    }
    packages = [ordered]@{
        codex = [ordered]@{
            version = (Get-Content (Join-Path $CacheRoot "codex\VERSION") -Raw).Trim()
            files = @("cache/codex/openai-codex.tgz", "cache/codex/codex-linux-x64.tgz")
        }
        blender_mcp = [ordered]@{
            source = "vendor/blender-mcp/mcp.tar.gz"
            source_metadata = "vendor/blender-mcp/SOURCE.json"
            lock = "cache/python/blender-mcp/requirements.lock"
            wheelhouse = "cache/python/blender-mcp/wheels"
        }
        comfy_mcp = [ordered]@{
            lock = "cache/python/comfy-mcp/requirements.lock"
            wheelhouse = "cache/python/comfy-mcp/wheels"
        }
    }
    docker_images = $records
    docker_archive = if (Test-Path $ImageArchive) { "cache/docker/sandbox-images.tar" } else { $null }
    files = $files
}
Ensure-Dir (Split-Path $ManifestPath)
$temp = "$ManifestPath.tmp"
$manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temp -Encoding utf8
Move-Item -LiteralPath $temp -Destination $ManifestPath -Force
Write-Host "Cache manifest written: $ManifestPath"
Write-Host "Offline cache preparation complete."
