# Manifest-driven multi-DCC verify gate

Now the round-trip task declares its expected host state in `config/eval.yaml`
under `task.verify`. `prepare` bakes that block into the immutable run
manifest, and `ai-native-evals run verify <run-id>` re-checks the host with
**independent read-back only** (never the Agent exit code or prose):

```powershell
uv run ai-native-evals run prepare roundtrip-blender-ue5-v2 --agent codex-dcc
uv run ai-native-evals run start <run-id>
uv run ai-native-evals run wait <run-id>
uv run ai-native-evals run verify <run-id>   # exit 0/1 from host truth
```

- Blender side: opens the saved `roundtrip-v2.blend` headless
  (`blender --background --python blender_inspect_scene.py`) and checks the
  expected object name/type/location.
- UE5 side: starts a **fresh** unreal-mcp session and calls
  `find_actors` (with the plugin-required `tag`/`collision_channels`),
  `get_actor_transform`, `get_components`, and `get_properties` for the mesh.
- Runs whose manifest has no `verify` block are refused (exit 1), so a gate
  can never silently pass on stale/self-reported state.

Scorer module: `src/ai_native_evals/scorers/multi_dcc_host_verifier.py`
(sync core `verify_host_state` + Inspect `@scorer`).
Task factory: `src/ai_native_evals/tasks/multi_dcc_roundtrip.py`.

## Proven run (2026-09-07)

`EvalRuns/roundtrip-blender-ue5-v2-56c8dd9cdd`

- agent exit 0 in the Docker sandbox (Codex 0.153.4, all-mcp image)
- `run verify` **passed**: `RoundtripV2Cube` MESH at (0,0,2) in
  `evidence/roundtrip-v2.blend` (96,917 bytes); UE5 `RoundtripV2Actor`
  (StaticMeshActor_1) at (0,0,200), components `StaticMeshComponent0` +
  `RoundtripV2Mesh`, mesh `/Engine/BasicShapes/Cube.Cube`.
- Hosts still running at verification time: Blender bridge :9876,
  UnrealEditor MCP :8000.
