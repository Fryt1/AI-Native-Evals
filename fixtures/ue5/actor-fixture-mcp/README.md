# UE5 Actor Fixture Project

Canonical small UE5 project used to verify the generic `host_operation` Workflow and UE5 Editor Python Toolset.

## Project

```text
ActorFixture.uproject
Config/DefaultEngine.ini
Content/AINativeActorE2E.umap
```

The `.umap` is the reproducible fixture input. Unreal-generated folders such as `DerivedDataCache/`, `Intermediate/`, and `Saved/` are local editor state and ignored.

## Acceptance

From the repository root:

```powershell
python scripts/e2e/ue5/run_actor_operation_e2e.py --timeout 120
```

The runner launches `UnrealEditor.exe` once to resolve the fixture Level, records native-window evidence, then runs the Actor mutation through `UnrealEditor-Cmd.exe` and validates the read-back Transform.
