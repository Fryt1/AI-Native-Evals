ARG PYTHON_BASE_IMAGE=python:3.12-slim
ARG NODE_BASE_IMAGE=node:22-bookworm
ARG OFFLINE=0

FROM ${PYTHON_BASE_IMAGE} AS blender-mcp

ARG OFFLINE
ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG COMFY_MCP_VERSION=0.10.0
ARG COMFY_CLI_VERSION=1.18.0

COPY vendor/blender-mcp/mcp.tar.gz /tmp/blender-mcp.tar.gz
COPY cache/python/blender-mcp/requirements.lock /tmp/wheels/blender/requirements.lock
COPY cache/python/blender-mcp/wheels/ /tmp/wheels/blender/
COPY cache/python/comfy-mcp/requirements.lock /tmp/wheels/comfy/requirements.lock
COPY cache/python/comfy-mcp/wheels/ /tmp/wheels/comfy/

RUN python -m venv /opt/blender-mcp \
    && mkdir -p /tmp/blender-mcp \
    && tar -xzf /tmp/blender-mcp.tar.gz -C /tmp/blender-mcp \
    && if [ "${OFFLINE}" = "1" ]; then \
         /opt/blender-mcp/bin/pip install --no-cache-dir --no-index \
           --find-links=/tmp/wheels/blender setuptools==84.0.0 \
         && /opt/blender-mcp/bin/pip install --no-cache-dir --no-index \
           --find-links=/tmp/wheels/blender -r /tmp/wheels/blender/requirements.lock \
         && /opt/blender-mcp/bin/pip install --no-cache-dir --no-index \
           --find-links=/tmp/wheels/blender --no-build-isolation --no-deps /tmp/blender-mcp/mcp; \
       else \
         /opt/blender-mcp/bin/pip install --no-cache-dir --upgrade pip \
         && /opt/blender-mcp/bin/pip install --no-cache-dir \
           --index-url="${PYPI_INDEX_URL}" "mcp[cli]>=1.2.0,<2" docutils pyyaml \
         && /opt/blender-mcp/bin/pip install --no-cache-dir --no-deps /tmp/blender-mcp/mcp; \
       fi

RUN python -m venv /opt/comfy-mcp \
    && if [ "${OFFLINE}" = "1" ]; then \
         /opt/comfy-mcp/bin/pip install --no-cache-dir --no-index \
           --find-links=/tmp/wheels/comfy -r /tmp/wheels/comfy/requirements.lock; \
       else \
         /opt/comfy-mcp/bin/pip install --no-cache-dir --upgrade pip \
         && /opt/comfy-mcp/bin/pip install --no-cache-dir \
           --index-url="${PYPI_INDEX_URL}" \
           "comfy-mcp==${COMFY_MCP_VERSION}" "comfy-cli==${COMFY_CLI_VERSION}"; \
       fi \
    && rm -rf /tmp/blender-mcp /tmp/blender-mcp.tar.gz /tmp/wheels


FROM ${NODE_BASE_IMAGE} AS node-runtime

FROM ${PYTHON_BASE_IMAGE}

# Use the Python base as the final image so the MCP venv and its glibc match.
# Copy only the Node runtime from the local Node/Gateway base; the
# Agent still remains a separate process from the Gateway container.
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=blender-mcp /opt/blender-mcp /opt/blender-mcp
COPY --from=blender-mcp /opt/comfy-mcp /opt/comfy-mcp

ARG CODEX_VERSION=0.153.4
# The pinned Node base already contains git. Copy the small runtime and its
# helpers instead of installing Debian packages during an image build; this
# keeps both online and offline builds independent of apt network access.
COPY --from=node-runtime /usr/bin/git /usr/bin/git
COPY --from=node-runtime /usr/lib/git-core /usr/lib/git-core
COPY --from=node-runtime /usr/share/git-core /usr/share/git-core
COPY --from=node-runtime /lib/x86_64-linux-gnu/libpcre2-8.so.0 /lib/x86_64-linux-gnu/libpcre2-8.so.0
RUN git --version

RUN groupadd --gid 1000 node \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash node

WORKDIR /workspace
ENV CODEX_HOME=/opt/codex-home
ENV PATH="/opt/blender-mcp/bin:/opt/comfy-mcp/bin:${PATH}"
ENV EVAL_MODEL=deepseek/deepseek-v4-flash
ENV EVAL_WIRE_API=responses
ENV EVAL_GATEWAY_URL=http://llm-gateway:8080/v1
ENV EVAL_REASONING_EFFORT=high
ENV BLENDER_MCP_HOST=host.docker.internal
ENV BLENDER_MCP_PORT=9876
ENV EVAL_ENABLE_BLENDER_MCP=1

COPY cache/codex/openai-codex.tgz /tmp/openai-codex.tgz
COPY cache/codex/codex-linux-x64.tgz /tmp/codex-linux-x64.tgz

USER root
RUN mkdir -p /usr/local/lib/node_modules/@openai/codex \
    && tar -xzf /tmp/openai-codex.tgz \
       -C /usr/local/lib/node_modules/@openai/codex \
       --strip-components=1 \
    && mkdir -p /usr/local/lib/node_modules/@openai/codex-linux-x64 \
    && tar -xzf /tmp/codex-linux-x64.tgz \
       -C /usr/local/lib/node_modules/@openai/codex-linux-x64 \
       --strip-components=1 \
    && ln -sf ../lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && mkdir -p /opt/codex-home /workspace \
    && chown -R node:node /opt/codex-home /workspace /opt/blender-mcp \
    && rm -f /tmp/openai-codex.tgz /tmp/codex-linux-x64.tgz

COPY docker/codex-agent/entrypoint.sh /usr/local/bin/ai-native-codex-entrypoint
COPY docker/codex-agent/render_codex_config.mjs /usr/local/lib/ai-native/render_codex_config.mjs
RUN chmod +x /usr/local/bin/ai-native-codex-entrypoint

USER node
ENTRYPOINT ["/usr/local/bin/ai-native-codex-entrypoint"]
