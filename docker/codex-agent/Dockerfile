ARG PYTHON_BASE_IMAGE=python:3.12-slim
ARG NODE_BASE_IMAGE=node:22-bookworm

FROM ${PYTHON_BASE_IMAGE} AS blender-mcp

COPY vendor/blender-mcp/mcp.tar.gz /tmp/blender-mcp.tar.gz

RUN python -m venv /opt/blender-mcp \
    && /opt/blender-mcp/bin/pip install --no-cache-dir --upgrade pip \
    && mkdir -p /tmp/blender-mcp \
    && tar -xzf /tmp/blender-mcp.tar.gz -C /tmp/blender-mcp \
    && /opt/blender-mcp/bin/pip install --no-cache-dir /tmp/blender-mcp/mcp \
    && rm -rf /tmp/blender-mcp /tmp/blender-mcp.tar.gz


FROM ${NODE_BASE_IMAGE}

ARG CODEX_VERSION=0.153.4
ARG NPM_REGISTRY=https://registry.npmjs.org

WORKDIR /workspace
ENV CODEX_HOME=/opt/codex-home
ENV PATH="/opt/blender-mcp/bin:${PATH}"
ENV EVAL_MODEL=deepseek/deepseek-v4-flash
ENV EVAL_WIRE_API=responses
ENV EVAL_GATEWAY_URL=http://llm-gateway:8080/v1
ENV EVAL_REASONING_EFFORT=high
ENV BLENDER_MCP_HOST=host.docker.internal
ENV BLENDER_MCP_PORT=9876
ENV EVAL_ENABLE_BLENDER_MCP=1

COPY --from=blender-mcp /opt/blender-mcp /opt/blender-mcp

RUN npm install --global --registry=${NPM_REGISTRY} @openai/codex@${CODEX_VERSION} \
    && mkdir -p /opt/codex-home /workspace \
    && chown -R node:node /opt/codex-home /workspace /opt/blender-mcp

COPY docker/codex-agent/entrypoint.sh /usr/local/bin/ai-native-codex-entrypoint
RUN chmod +x /usr/local/bin/ai-native-codex-entrypoint

USER node
ENTRYPOINT ["/usr/local/bin/ai-native-codex-entrypoint"]
