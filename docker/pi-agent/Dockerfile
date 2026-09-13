# pi ships as a published CLI that takes a prompt and exits (`--print`), so it
# needs no protocol bridge: the run's entrypoint calls it directly and its own
# JSON event stream is what the trace parser reads. An Agent needing a
# long-lived protocol, as DSH does, instead needs a runner like
# docker/dsh-agent/acp-runner.mjs.
#
# Built from the plain Node image, not the Codex one: pi is installed with npm,
# and the Codex image carries only Node and git -- it installs Codex by unpacking
# a prepared tarball. Which base an Agent needs is this Dockerfile's decision,
# which is why the profile declares no base image.
ARG NODE_BASE_IMAGE=node:22-bookworm
FROM ${NODE_BASE_IMAGE}

ARG PI_VERSION=0.85.1
ARG NPM_REGISTRY=https://registry.npmmirror.com
# The version travels inside the image as a label: a tag is a claim that can be
# re-pointed, the label cannot.
LABEL ai.native.agent="pi" \
      ai.native.agent.version="$PI_VERSION"

RUN npm install --global --no-fund --no-audit --registry="${NPM_REGISTRY}" \
        @earendil-works/pi-coding-agent@${PI_VERSION} \
    && pi --version \
    # pi writes its config and sessions here; the run user owns the directory,
    # and nothing reads the host's own pi configuration.
    && mkdir -p /opt/pi-home \
    && chown -R node:node /opt/pi-home

COPY docker/pi-agent/entrypoint.sh /usr/local/bin/ai-native-pi-entrypoint
RUN chmod 0755 /usr/local/bin/ai-native-pi-entrypoint

ENV PI_CODING_AGENT_DIR=/opt/pi-home/agent
ENV PI_TELEMETRY=0

USER node
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/ai-native-pi-entrypoint"]
