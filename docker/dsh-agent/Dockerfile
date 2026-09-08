ARG NODE_BASE_IMAGE=node:22-bookworm
FROM ${NODE_BASE_IMAGE}

ARG DSH_COMMIT=unknown
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG DSH_PREINSTALLED=0
WORKDIR /opt/dsh

# The build context is the real DSH repository (D:\work\AI-Native\dsh), not
# AI-Native-Evals. This keeps the Agent image independent from the evaluator.
COPY . /opt/dsh

RUN if [ "${DSH_PREINSTALLED}" != "1" ]; then \
        corepack enable \
        && corepack prepare pnpm@11.7.0 --activate \
        && pnpm config set registry "${NPM_REGISTRY}" \
        && CI=1 pnpm install --frozen-lockfile \
            --filter @deepseek-ai/dsh... \
            --filter @deepseek-ai/dsh-acp-app... \
        && npm install --no-save --no-fund --no-audit --prefix /opt/dsh \
            --registry="${NPM_REGISTRY}" tsx@4.22.4; \
    else \
        echo "Using preinstalled DSH node_modules"; \
    fi \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash eval-agent 2>/dev/null || true \
    && mkdir -p /tmp/dsh-home /workspace \
    && chown -R 1000:1000 /opt/dsh /tmp/dsh-home /workspace

ENV PATH=/opt/dsh/node_modules/.bin:${PATH}
ENV DSH_HOME=/tmp/dsh-home
ENV DSH_MODEL=deepseek/deepseek-v4-flash
ENV DSH_REASONING_EFFORT=high
ENV EVAL_GATEWAY_URL=http://llm-gateway:8080/v1
ENV EVAL_MODEL=deepseek/deepseek-v4-flash
ENV EVAL_WIRE_API=responses
ENV EVAL_REASONING_EFFORT=high
ENV EVAL_MCP_SERVERS_FILE=/run-config/mcp-servers.json
ENV EVAL_DSH_MCP_SERVERS_FILE=/run-config/dsh-mcp-servers.json
ENV EVAL_WORKDIR=/workspace
ENV DSH_COMMIT=${DSH_COMMIT}

USER 1000:1000
ENTRYPOINT ["node", "--import", "tsx/esm", "/opt/dsh/apps/cli/src/bin.ts", "--profile", "acp"]
