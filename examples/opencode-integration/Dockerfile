# syntax=docker/dockerfile:1.7
#
# OpenCode coding agent inside a CubeSandbox template.
#
# The image installs Node.js 24 and the OpenCode CLI on top of the official
# CubeSandbox base image. The inherited cube-entrypoint keeps envd running on
# :49983 so Cube can use the standard readiness probe.
#
# Build:
#   docker build -t opencode-cube:latest examples/opencode-integration
#
# Register as a Cube template:
#   cubemastercli tpl create-from-image \
#     --image <your-registry>/opencode-cube:latest \
#     --writable-layer-size 4G \
#     --expose-port 49983 \
#     --probe       49983 \
#     --probe-path  /health

ARG CUBE_BASE_IMAGE=ghcr.io/tencentcloud/cubesandbox-base:2026.16
FROM ${CUBE_BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG NODE_MAJOR=24
ARG OPENCODE_VERSION=latest

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        gnupg \
        jq \
        less \
        procps \
        python3 \
        python3-pip \
        ripgrep \
    && curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install OpenCode in its own layer so bumping OPENCODE_VERSION does not
# invalidate the slow OS + Node.js layer above.
# --ignore-scripts skips npm postinstall scripts to keep the template image
# build hermetic and avoid running arbitrary code during image construction;
# the opencode --version smoke test below still validates the installation.
RUN npm install -g --ignore-scripts "opencode-ai@${OPENCODE_VERSION}" \
    && opencode --version \
    && npm cache clean --force \
    && rm -rf /root/.npm

ENV OPENCODE_AGENT_DIR=/root/.pi/agent \
    OPENCODE_SESSION_DIR=/root/.pi/agent/sessions \
    OPENCODE_TELEMETRY=0 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

RUN mkdir -p /workspace "${OPENCODE_SESSION_DIR}" \
    && printf '%s\n' \
        '{' \
        '  "quietStartup": true,' \
        '  "enableInstallTelemetry": false' \
        '}' \
        > "${OPENCODE_AGENT_DIR}/settings.json"

WORKDIR /workspace

EXPOSE 49983
