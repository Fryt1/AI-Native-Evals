# UE5 / Blender MCP E2E verification (real host tasks, sandboxed agent)

Proven 2026-09-07 on this machine. The evaluation agent runs inside the Docker
sandbox; Blender 5.2.1 and UnrealEditor 5.8.2 stay on the Windows host and are
mutated only through standard MCP servers.

## Environment used

```text
Windows 11 + WSL2 Docker (Ubuntu-20.04 engine; Docker Desktop 29.7.2)
Agent image: ai-native-codex-agent:all-mcp (Codex 0.153.4 + official servers)
Gateway:    ai-native-llm-gateway:local
Model:      deepseek/deepseek-v4-flash via local gateway / Sub2API (responses)
Blender:    E:\blender\blender.exe 5.2.1 + Blender Lab MCP Add-on bridge :9876
Unreal:     D:\UnrealEngine\ue5.8.2\UnrealEngine (ModelContextProtocol + AllToolsets)
UE project: fixtures/ue5/actor-fixture-mcp (this repo, plugins pre-enabled)
```

## Provenance of the helpers used for the E2E runs

Blender bridge launch (host):

```powershell
# add-on installed under Blender 5.2; the vendored project helper starts the
# in-Blender socket bridge without GUI:
E:\blender\blender.exe --background --python <game-engine>/artifacts/leopard2a4/mcp_bootstrap.py
```

UE MCP launch (host) uses the MCP plugin command-line switches that were
verified from the engine source:

```text
UnrealEditor.exe <project>.uproject -ModelContextProtocolStartServer -ModelContextProtocolPort=8000
```

The project fixture is committed under `fixtures/ue5/actor-fixture-mcp` with
`ModelContextProtocol` and `AllToolsets` enabled so a fresh run is reproducible.

## Run manifests (kept under ../EvalRuns)

- `EvalRuns/blender-cube-e2e-20260907/`  (status completed, exit 0)
- `EvalRuns/ue5-cube-e2e-20260907/`      (status completed, exit 0)
- each has `evidence/*.json` and `trace/agent-container.log`

## What the agent actually did (from the persisted container logs)

Blender task (`blender` MCP server, stdio):
1. `blender.get_objects_summary` -> read scene
2. removed the stale non-cube `EvalCube`, created a real unit cube
   (`data_name=EvalCube`, 8 verts/6 faces, 1.0x1.0x1.0, selected, visible)
3. verified with `get_object_detail_summary` + final summary

UE task (`unreal-mcp` streamable HTTP):
1. `list_toolsets` / `describe_toolset` discovery
2. `SceneTools.find_actors` (empty) -> `SceneTools.add_to_scene_from_class`
   created `StaticMeshActor_0` named `EvalUECubeActor` at (0,0,100)
3. `PrimitiveTools.add_cube` added `CubeMesh` with 100x100x100
4. verified actor transform (0,0,100), components list, and cube static mesh
   `/Engine/BasicShapes/Cube.Cube`

Both runs ended with the Docker network/containers removed automatically.
Host Blender/UE processes were stopped after verification.

## Reproduce

1. Ensure the two host services are listening:
   - Blender MCP bridge on the host TCP port 9876
   - UE5 MCP endpoint on http://<host-reachable>:8000/mcp
2. Start the run through the CLI with a manifest whose `mcp_servers` uses the
   profile servers in `config/eval.yaml`:
   - blender: stdio command `/opt/blender-mcp/bin/blender-mcp`, env
     `BLENDER_MCP_HOST=host.docker.internal`, `BLENDER_MCP_PORT=9876`
   - unreal-mcp: streamable-http
     `http://host.docker.internal:8000/mcp`
3. `uv run ai-native-evals run start <run-id>` then `wait`.
