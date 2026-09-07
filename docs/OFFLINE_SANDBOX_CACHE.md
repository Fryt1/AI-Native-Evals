# Offline sandbox cache

The evaluation sandbox has a reproducible local dependency cache. It is split
into two kinds of data:

- **Git-tracked metadata**: version files, dependency locks, source provenance,
  and `cache/manifest.json`.
- **Local large inputs**: Docker image archive, Python wheels, and Codex tarballs.
  These stay in the checkout but are ignored by Git. Back them up separately if
  the checkout is moved to another machine.

## Current cache contents

Generated on 2026-09-07 for `linux/amd64`:

```text
Python base:       python:3.12-slim
Node base:         node:22-bookworm
Codex:             0.153.4
Blender MCP:       source commit 4309a39646e644261624bfcd2bca669b343b7621
Blender MCP API:   MCP v1 dependencies in cache/python/blender-mcp
Comfy MCP:         0.10.0
Comfy CLI:         1.18.0
Gateway:           ai-native-llm-gateway:local
Agent images:      ai-native-codex-agent:local, ai-native-codex-agent:all-mcp
```

The exact Docker image IDs, repository digests, file sizes, and SHA256 values
are in `cache/manifest.json`.

## First-time preparation (VPN/network available)

From the repository root:

```powershell
pwsh -NoProfile -File .\tools\prepare-offline-cache.ps1
```

This will:

1. reuse or download the pinned Codex packages;
2. ensure the pinned Python and Node base images are local;
3. download binary-only Linux wheels into the Blender MCP and Comfy MCP
   wheelhouses;
4. save the local Docker images to `cache/docker/sandbox-images.tar`;
5. write a hash-checked `cache/manifest.json`.

To intentionally regenerate Python locks from a known-good all-MCP image:

```powershell
pwsh -NoProfile -File .\tools\prepare-offline-cache.ps1 -RegenerateLocks
```

Do this only when intentionally changing dependency versions; review the diff
in the lock files afterward.

## Verify the cache

```powershell
pwsh -NoProfile -File .\tools\verify-cache.ps1 `
  -Distro Ubuntu-20.04 `
  -RequireDockerArchive `
  -CheckDockerImages
```

The checker verifies required files, wheelhouse presence, every recorded
SHA256, and (when requested) that all expected images are loaded in the WSL
Docker daemon.

## Build completely offline

```powershell
pwsh -NoProfile -File .\tools\build-sandbox-images.ps1 `
  -Distro Ubuntu-20.04 `
  -Offline

# Include Blender MCP and Comfy MCP in the Agent image:
pwsh -NoProfile -File .\tools\build-sandbox-images.ps1 `
  -Distro Ubuntu-20.04 `
  -Offline `
  -IncludeBlenderMcp
```

The offline build loads the Docker archive first and then uses
`docker build --network none --pull=false`. It does not use Docker Hub, PyPI,
npm, or apt. The current offline build was validated successfully and the
resulting image includes `git`, Codex 0.153.4, Blender MCP, and Comfy MCP.

## What is deliberately not cached in Git

```text
cache/docker/sandbox-images.tar
cache/python/*/wheels/*.whl
cache/codex/*.tgz
config/.env.local
API keys and gateway credentials
```

The gateway credentials remain local in `config/.env.local`; the cache scripts
never read or copy their secret values into the manifest.

## Updating the cache after a dependency change

1. Change the Dockerfile or pinned version deliberately.
2. Run `prepare-offline-cache.ps1` with the network available.
3. Run `verify-cache.ps1`.
4. Run the offline build with `-Offline`.
5. Smoke-test the image (`git`, `codex --version`, MCP `--help`).
6. Commit only the Dockerfile/script/lock/manifest metadata; keep large cache
   files local.

The cache is platform-specific. A cache generated for `linux/amd64` should not
be treated as a drop-in cache for another architecture.
