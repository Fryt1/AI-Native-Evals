# Multi-DCC sandbox roundtrip run

## Result (real host read-back, not agent self-report)

Run: `EvalRuns/roundtrip-blender-ue5-20260907`
Status: completed (agent exit 0) + host-side verification `succeeded`

Agent image: `ai-native-codex-agent:all-mcp` (Codex 0.153.4)
Hosts: Blender 5.2.1 bridge :9876, UnrealEditor 5.8.2 MCP :8000

### Blender (verified by opening the saved .blend headless)
- object `RoundtripCube` MESH at (0,0,1), scale (1,1,1)
- saved scene: evidence/roundtrip.blend (96,581 bytes)

### UE5 (verified by an independent unreal-mcp session)
- actor `RoundtripActor` = StaticMeshActor_0 at (0,0,100)
- components include `RoundtripMesh`
- RoundtripMesh StaticMesh = /Engine/BasicShapes/Cube.Cube

Evidence: evidence/roundtrip.blend, evidence/roundtrip-summary.json (agent),
evidence/verification.json (host read-back)

## Why the shared-evidence path matters

Blender's `execute_blender_code` runs inside the *host* Blender process. The
agent must write through the same Windows host path that the Docker
`/workspace/evidence` mount maps to (the run dir under EvalRuns). Container-only
paths are not visible to the host DCC.
