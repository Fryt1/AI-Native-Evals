# DSH Agent image

This image runs the real `D:\work\AI-Native\dsh` source in its ACP profile.
The evaluation harness launches it only inside a per-run Docker network and
sends standard Agent Client Protocol JSON-RPC over stdin/stdout. It does not
use the small `AI-Native-DSH` Game Engine plugin as a fake Agent.

Build from the evaluator repository:

```powershell
pwsh -File .\tools\build-sandbox-images.ps1 -IncludeDsh -UseMirror
```

The build context is `..\dsh`; the image is tagged
`ai-native-dsh-agent:local` and uses the DSH commit as a Docker label/env value.
