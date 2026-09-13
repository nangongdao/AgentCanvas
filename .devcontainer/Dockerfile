FROM mcr.microsoft.com/devcontainers/python:1-3.12-bullseye

# Install Node.js 20.x
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g pnpm

# Install uv
RUN pip install --no-cache-dir uv

# Install PostgreSQL client
RUN apt-get update && apt-get install -y postgresql-client

# Install additional tools
RUN apt-get install -y \
    git \
    curl \
    wget \
    vim \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /workspace

# Non-root user
USER vscode
