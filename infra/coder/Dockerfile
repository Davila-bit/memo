# hearth-dev:latest: the Coder workspace image for Memo.
# Build: docker build -t hearth-dev:latest infra/coder
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Base tools + Python 3.12 (Ubuntu 24.04 default) + ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl wget gnupg git jq ffmpeg build-essential sudo \
      python3 python3-venv python3-pip python3-dev \
      less vim-tiny openssh-client unzip zip locales tzdata procps iproute2 \
    && locale-gen en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

# Node 24.x (NodeSource) + corepack pnpm
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g corepack@latest \
    && corepack enable \
    && corepack prepare pnpm@latest --activate

# GitHub CLI
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Playwright system deps for chromium (browsers themselves are installed per project)
RUN npx -y playwright@latest install-deps chromium && rm -rf /var/lib/apt/lists/* /root/.npm

# uv (system-wide)
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh

# User coder uid/gid 1000 (replace the stock ubuntu user), passwordless sudo
RUN userdel -r ubuntu 2>/dev/null || true \
    && groupadd -g 1000 coder \
    && useradd -m -u 1000 -g 1000 -s /bin/bash coder \
    && echo "coder ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/coder \
    && chmod 0440 /etc/sudoers.d/coder

ENV LANG=en_US.UTF-8 \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    UV_LINK_MODE=copy

USER coder
# Pre-fetch pnpm for the coder user so `pnpm -v` works offline
RUN corepack prepare pnpm@latest --activate && pnpm -v
WORKDIR /home/coder
