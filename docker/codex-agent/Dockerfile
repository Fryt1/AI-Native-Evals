ARG PYTHON_BASE_IMAGE=python:3.12-slim
ARG NODE_BASE_IMAGE=node:22-bookworm

FROM ${PYTHON_BASE_IMAGE} AS blender-mcp

ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG COMFY_MCP_VERSION=0.10.0
ARG COMFY_CLI_VERSION=1.18.0

COPY vendor/blender-mcp/mcp.tar.gz /tmp/blender-mcp.tar.gz

RUN python -m venv /opt/blender-mcp \
    && /opt/blender-mcp/bin/pip install --no-cache-dir --upgrade pip \
    && mkdir -p /tmp/blender-mcp \
    && tar -xzf /tmp/blender-mcp.tar.gz -C /tmp/blender-mcp \
    && /opt/blender-mcp/bin/pip install --no-cache-dir /tmp/blender-mcp/mcp \
    && /opt/blender-mcp/bin/pip install --no-cache-dir \
       --index-url="${PYPI_INDEX_URL}" \
       "comfy-mcp==${COMFY_MCP_VERSION}" \
       "comfy-cli==${COMFY_CLI_VERSION}" \
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
COPY cache/codex/openai-codex.tgz /tmp/openai-codex.tgz
COPY cache/codex/codex-linux-x64.tgz /tmp/codex-linux-x64.tgz

USER root
RUN npm install --global --offline --omit=optional /tmp/openai-codex.tgz \
    && mkdir -p /usr/local/lib/node_modules/@openai/codex-linux-x64 \
    && tar -xzf /tmp/codex-linux-x64.tgz \
       -C /usr/local/lib/node_modules/@openai/codex-linux-x64 \
       --strip-components=1 \
    && mkdir -p /opt/codex-home /workspace \
    && chown -R node:node /opt/codex-home /workspace /opt/blender-mcp \
    && rm -f /tmp/openai-codex.tgz /tmp/codex-linux-x64.tgz

COPY docker/codex-agent/entrypoint.sh /usr/local/bin/ai-native-codex-entrypoint
COPY docker/codex-agent/render_codex_config.mjs /usr/local/lib/ai-native/render_codex_config.mjs
RUN chmod +x /usr/local/bin/ai-native-codex-entrypoint

USER node
ENTRYPOINT ["/usr/local/bin/ai-native-codex-entrypoint"]
