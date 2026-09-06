# Codex Agent Docker image

This image runs Codex as an evaluation Agent. It does not contain Blender/UE5
and it does not read the host user's `~/.codex` directory.

The entrypoint creates an isolated `CODEX_HOME` and points Codex at the
AI-Native LLM Gateway. The Gateway credential is supplied as
`EVAL_GATEWAY_API_KEY`; upstream model credentials never enter this container.

The image also includes the **official Blender Lab MCP Server**. The server is
launched as a normal stdio MCP server by Codex and connects to the Blender MCP
Add-on running in the host Blender process. This is not a custom adapter:

```text
Codex MCP client
    -> official blender-mcp (stdio)
    -> Blender Add-on bridge (host TCP)
    -> bpy / real Blender
```

The host endpoint is configured independently for each container:

```text
BLENDER_MCP_HOST=host.docker.internal
BLENDER_MCP_PORT=9876
```

For the WSL2 Docker engine, `host.docker.internal` must resolve to the Windows
host or be replaced with the reachable Windows host gateway address. The
Blender Add-on must be enabled and its bridge must be running before a tool
call can succeed.

The container is the outer sandbox. The entrypoint disables Codex's inner OS
sandbox by default to avoid nested user-namespace failures inside Docker.

## Build

Build from the repository root so the Dockerfile can use its pinned official
Blender MCP source during the image build:

```powershell
wsl.exe -d Ubuntu-20.04 -- docker build \
  -f /mnt/d/work/AI-Native/AI-Native-Evals/docker/codex-agent/Dockerfile \
  -t ai-native-codex-agent:local \
  /mnt/d/work/AI-Native/AI-Native-Evals
```

The official Blender MCP source is pinned in the Dockerfile by commit SHA.

## Disable Blender MCP for a diagnostic run

```text
EVAL_ENABLE_BLENDER_MCP=0
```
