ARG NODE_BASE_IMAGE=node:22-bookworm
FROM ${NODE_BASE_IMAGE}

ARG DSH_COMMIT=unknown
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG DSH_PREINSTALLED=0
# Extra plugins to install, as a space-separated list of package specs. A plugin
# is a package the host loads by name, so making it available is an install --
# what the run then *loads* is a separate decision, made by its configuration.
ARG DSH_PLUGINS=""
WORKDIR /opt/dsh

# The build context is a DSH source repository, not AI-Native-Evals. This keeps
# the Agent image independent from the evaluator. The location of that
# repository is a local deployment detail supplied by the build script.
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
    && if [ -n "${DSH_PLUGINS}" ]; then \
        echo "Installing plugins: ${DSH_PLUGINS}" \
        && npm install --no-save --no-fund --no-audit --prefix /opt/dsh \
            --registry="${NPM_REGISTRY}" ${DSH_PLUGINS}; \
    fi \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash eval-agent 2>/dev/null || true \
    && mkdir -p /tmp/dsh-home /workspace \
    && chown -R 1000:1000 /opt/dsh /tmp/dsh-home /workspace

# The native workspace package carries no `bin/`: the .node addons are a CI
# artifact, published separately. Without them the CLI aborts at startup with
# "Cannot find module .../system.node", which reads like a plugin failure and is
# not one. Installing the published copy fills the gap for this image's platform.
#
# Installed under /opt/native-stage, not /opt/dsh: that directory is a pnpm
# workspace, and npm cannot read its `workspace:` protocol specs.
ARG NATIVE_ADDON_VERSION=0.1.2
RUN mkdir -p /opt/native-stage \
    && cd /opt/native-stage \
    && npm install --no-save --no-fund --no-audit \
        --registry="${NPM_REGISTRY}" \
        "@deepseek-ai/node-addon-system-linux-x64@${NATIVE_ADDON_VERSION}" \
    && cp -r /opt/native-stage/node_modules/@deepseek-ai/node-addon-system-linux-x64/bin \
        /opt/dsh/native/system/packages/linux-x64/bin \
    && rm -rf /opt/native-stage \
    && chown -R 1000:1000 /opt/dsh/native/system/packages/linux-x64/bin \
    && node -e "require('/opt/dsh/native/system/packages/linux-x64/bin/glibc/system.node')" \
    && echo "native addons loaded"

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
