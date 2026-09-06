FROM node:22-bookworm

ARG CODEX_VERSION=0.153.4

WORKDIR /workspace
ENV CODEX_HOME=/opt/codex-home
ENV EVAL_MODEL=deepseek/deepseek-v4-flash
ENV EVAL_WIRE_API=responses
ENV EVAL_GATEWAY_URL=http://llm-gateway:8080/v1
ENV EVAL_REASONING_EFFORT=high

RUN npm install --global @openai/codex@${CODEX_VERSION} \
    && mkdir -p /opt/codex-home /workspace \
    && chown -R node:node /opt/codex-home /workspace

COPY entrypoint.sh /usr/local/bin/ai-native-codex-entrypoint
RUN chmod +x /usr/local/bin/ai-native-codex-entrypoint

USER node
ENTRYPOINT ["/usr/local/bin/ai-native-codex-entrypoint"]
