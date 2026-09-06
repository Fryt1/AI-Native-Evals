# Codex Agent Docker image

This image runs Codex as an evaluation Agent. It does not contain Blender/UE5
and it does not read the host user's `~/.codex` directory.

The entrypoint creates an isolated `CODEX_HOME` and points Codex at the
AI-Native LLM Gateway. The Gateway credential is supplied as
`EVAL_GATEWAY_API_KEY`; upstream model credentials never enter this container.

The full image also includes the standard MCP clients needed by the project:

```text
blender-host  -> official blender-mcp stdio server -> host Blender
comfyui-host  -> official comfy-mcp stdio server   -> host ComfyUI HTTP API
ue5-host      -> streamable HTTP                    -> host UE5 MCP endpoint
research-host -> streamable HTTP                    -> Hugging Face MCP
```

The per-run MCP profile is mounted read-only at
`/run-config/mcp-servers.json`. The Codex entrypoint renders it into the
container-local `CODEX_HOME/config.toml`; it never reads or modifies the host
Codex configuration. DSH receives the same descriptors in ACP's
`mcpServers` shape at `/run-config/dsh-mcp-servers.json`.

The Blender and ComfyUI stdio servers run inside the Agent image. Their target
applications remain on the host and are reached through `host.docker.internal`.
The UE5 and Hugging Face entries are standard streamable HTTP MCP connections.
No custom Agent-side socket client or protocol adapter is used.

The container is the outer sandbox. The entrypoint disables Codex's inner OS
sandbox by default to avoid nested user-namespace failures inside Docker.

## Build

Build from the repository root so the Dockerfile can use its pinned official
Blender MCP source during the image build:

```powershell
wsl.exe -d Ubuntu-20.04 -- docker build \
  -f /mnt/d/work/AI-Native/AI-Native-Evals/docker/codex-agent/Dockerfile \
  -t ai-native-codex-agent:all-mcp \
  /mnt/d/work/AI-Native/AI-Native-Evals
```

The official Blender MCP source is pinned in the Dockerfile by commit SHA. The official ComfyUI MCP and comfy-cli versions are pinned as build arguments.

## Disable Blender MCP for a diagnostic run

```text
EVAL_ENABLE_BLENDER_MCP=0
```
