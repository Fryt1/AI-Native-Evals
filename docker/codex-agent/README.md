# Codex Agent Docker image

This image runs Codex as an evaluation Agent. It does not contain Blender/UE5
and it does not read the host user's `~/.codex` directory.

The entrypoint creates an isolated `CODEX_HOME` and points Codex at the
AI-Native LLM Gateway. The Gateway credential is supplied as
`EVAL_GATEWAY_API_KEY`; upstream model credentials never enter this container.

The container is the outer sandbox. The entrypoint disables Codex's inner OS sandbox by default to avoid nested user-namespace failures inside Docker.
