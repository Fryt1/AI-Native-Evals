# A single-command Agent, from the Codex image so it can borrow Node and
# python3 rather than installing them. A real Agent replaces the COPY with its
# own installation step and points the entrypoint at its CLI.
ARG CODEX_BASE_IMAGE=ai-native-codex-agent:0.153.4
FROM ${CODEX_BASE_IMAGE}

ARG EXAMPLE_CLI_VERSION=1.0.0
# The version lives inside the image as a label. A tag is a claim that can be
# re-pointed; the label travels with the image.
LABEL ai.native.agent="example-cli" \
      ai.native.agent.version="$EXAMPLE_CLI_VERSION"

COPY docker/example-cli/entrypoint.mjs /usr/local/bin/example-cli-agent.mjs
USER root
RUN chmod 0644 /usr/local/bin/example-cli-agent.mjs
USER node

WORKDIR /workspace
ENTRYPOINT ["node", "/usr/local/bin/example-cli-agent.mjs"]
