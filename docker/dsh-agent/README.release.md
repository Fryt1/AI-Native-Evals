# DSH release image

`Dockerfile.release` packages the published `@deepseek-ai/dsh` CLI instead of
building the `D:\work\AI-Native\dsh` monorepo. It is a fast compatibility path
for environments where the source image's pnpm build is too expensive. The
source profile remains `profiles/agents/dsh.yaml` and is the preferred profile
when evaluating the exact local DSH commit.

```powershell
pwsh -File .\tools\build-sandbox-images.ps1 -IncludeDshRelease -UseMirror
# 可选：指定发布版本
pwsh -File .\tools\build-sandbox-images.ps1 -IncludeDshRelease -DshVersion 0.1.2-rc.1 -UseMirror
```
