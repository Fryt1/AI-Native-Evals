# DSH Agent image

This image runs a real DSH source checkout in its ACP profile. The evaluation
harness launches it only inside a per-run Docker network and sends standard
Agent Client Protocol JSON-RPC over stdin/stdout. It does not use a Game Engine
plugin as a fake Agent.

Build from the evaluator repository:

```powershell
pwsh -File .\tools\build-sandbox-images.ps1 -IncludeDsh -UseMirror
```

The image is tagged `ai-native-dsh-agent:local` and uses the DSH commit as a
Docker label/env value.

The build context is the DSH source repository, located by
`paths.source_roots.dsh_runtime` in `config/eval.yaml`. That repository is not
part of this project and its location is a local deployment detail.
