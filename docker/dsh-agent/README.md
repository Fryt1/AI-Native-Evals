# DSH release image

`Dockerfile.release` packages the published `@deepseek-ai/dsh` CLI. It is the DSH
Agent image, built from `profiles/agents/dsh-release.yaml`.

The evaluation harness launches it only inside a per-run Docker network and drives
it with standard Agent Client Protocol JSON-RPC over stdin/stdout. It does not use
a Game Engine plugin as a fake Agent, and it does not use a private socket
adapter: MCP servers are declared through the standard ACP `McpServer[]` shape.

```powershell
# 从评测仓库根目录构建
.\tools\eval.ps1 build -Agent dsh-release -UseMirror

# 固定一个发布版本
.\tools\eval.ps1 build -Agent dsh-release -Version 0.1.2-rc.1
```

The build tool reads the profile's `build` block, so it knows only that this
Agent has a Dockerfile and what its version argument is called. Adding a
different Agent version, or a different Agent, is a profile edit.

## There is no source-build image

An earlier revision built a second image from a DSH source checkout at
`paths.source_roots.dsh_runtime`, tagged `ai-native-dsh-agent:local`, to reproduce
one exact commit. It was removed. Making it work meant putting `tests`,
`website` and `benchmarks` into the build context and running a full `tsc`,
because the workspace's `lib/` output is a build product — and this repository
does not modify DSH's source, so that cost bought nothing. A self-authored DSH
plugin is delivered at run time by the profile's `attach` instead, which needs no
source image at all.
