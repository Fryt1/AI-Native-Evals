# AI-Native LLM Gateway

A thin protocol gateway for agent containers. It is **not** an Agent runtime and it does not replace Codex or DSH model providers.

## Northbound API

- `GET /health`
- `GET /v1/models`
- `POST /v1/responses`
- `POST /v1/chat/completions`

The gateway forwards both protocols to the configured upstream and keeps the upstream credential outside the Agent container. `Responses` is a first-class protocol for Codex; Chat Completions is available for DSH/provider routes that use it.

## Runtime configuration

The gateway reads:

```text
UPSTREAM_BASE_URL
UPSTREAM_API_KEY
UPSTREAM_WIRE_API=responses|chat
DEFAULT_MODEL (optional)
GATEWAY_API_KEY (optional northbound credential)
PORT=8080 (optional)
```

For this repository, `config/.env.local` is gitignored and may be used as the upstream env file when it contains `AI_NATIVE_EVALS_LLM_BASE_URL`, `AI_NATIVE_EVALS_LLM_API_KEY`, `AI_NATIVE_EVALS_LLM_MODEL`, and `AI_NATIVE_EVALS_LLM_WIRE_API`.

## Local Docker test through the WSL2 engine

The current Windows Docker Desktop installation is not required. Point the Windows Docker CLI at the WSL2 Docker daemon, build, and run:

```powershell
$wslIp = (wsl.exe -d Ubuntu-20.04 -- hostname -I).ToString().Trim().Split(' ')[0]
$env:DOCKER_HOST = "tcp://${wslIp}:2375"
docker build -t ai-native-llm-gateway:local .\gateway
docker run --rm -p 18080:8080 --env-file .\config\.env.local `
  -e GATEWAY_API_KEY=local-test-key `
  ai-native-llm-gateway:local
```

An Agent container on the same Docker network should use:

```text
http://ai-native-llm-gateway:8080/v1
```
