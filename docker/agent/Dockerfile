# syntax=docker/dockerfile:1.7
FROM node:22.22.0-bookworm-slim AS node
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7
ARG CODEX_VERSION=0.149.0
ARG CLAUDE_VERSION=2.1.270
ARG PI_VERSION=0.84.2
ARG HARBOR_VERSION=0.23.0
ARG AGENT_UID=10001
ARG AGENT_GID=10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/home/agent
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && apt-get update && apt-get install -y --no-install-recommends \
    bash build-essential ca-certificates curl file git jq patch ripgrep tini tmux unzip \
 && rm -rf /var/lib/apt/lists/* \
 && npm install --global --omit=dev --no-audit --no-fund \
    "@openai/codex@${CODEX_VERSION}" "@anthropic-ai/claude-code@${CLAUDE_VERSION}" \
    "@earendil-works/pi-coding-agent@${PI_VERSION}" \
 && npm cache clean --force
# Build context contains the locally built, digest-recorded AgentFlow wheel.
COPY wheels/ /tmp/wheels/
RUN python -m pip install --no-cache-dir /tmp/wheels/*.whl "harbor==${HARBOR_VERSION}" \
 && rm -rf /tmp/wheels \
 && groupadd --gid "${AGENT_GID}" agent \
 && useradd --uid "${AGENT_UID}" --gid "${AGENT_GID}" --create-home --shell /bin/bash agent \
 && mkdir -p /workspace /agentflow-runtime /run/secrets \
 && chown agent:agent /workspace /agentflow-runtime
USER agent:agent
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["codex", "--help"]
