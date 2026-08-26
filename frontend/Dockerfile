# AgentCanvas frontend image - build with Vite, serve static via nginx
FROM node:24-slim AS build

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@11.6.0 --activate

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm run build

FROM nginxinc/nginx-unprivileged:1.27.5-alpine AS serve

# The unprivileged base image defaults to USER 101, but the default static
# assets and nginx conf.d are root-owned. COPY runs as the builder (root), but
# RUN shell redirects execute as the active user, so switch to root to clean
# the default site and write the config, then drop back to 101 for the server.
USER root

# Drop the default static site and stream our built assets.
RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /app/dist /usr/share/nginx/html

# nginx config: dynamic Compose DNS + SPA fallback + SSE-friendly proxying.
RUN printf 'upstream agentcanvas_backend {\n\
    zone agentcanvas_backend 64k;\n\
    resolver 127.0.0.11 valid=5s ipv6=off;\n\
    server backend:8000 resolve;\n\
}\n\
\n\
server {\n\
    listen 8080;\n\
    server_name _;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
\n\
    location /api/ {\n\
        proxy_pass http://agentcanvas_backend;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header Host $host;\n\
        proxy_set_header X-Real-IP $remote_addr;\n\
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n\
        proxy_buffering off;\n\
        proxy_cache off;\n\
        proxy_read_timeout 3600s;\n\
        chunked_transfer_encoding on;\n\
    }\n\
\n\
    location /healthz {\n\
        proxy_pass http://agentcanvas_backend;\n\
    }\n\
\n\
    location ~ ^/(livez|readyz)$ {\n\
        proxy_pass http://agentcanvas_backend;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 8080

# Drop privileges back to the unprivileged user for the running server.
USER 101

CMD ["nginx", "-g", "daemon off;"]
