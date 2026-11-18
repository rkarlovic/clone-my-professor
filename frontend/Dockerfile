# CMP web image: the built pages served by Caddy, which also terminates HTTPS and proxies /api to
# the API container (infra/docker-compose.yml). Multi-stage, pinned bases, runs as non-root.

FROM node:24.11.1-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2.11.6-alpine
COPY --from=build /app/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
# Caddy keeps its certificates in /data (a volume); a non-root user may bind 80/443 with the
# capability the official image grants.
RUN addgroup -S cmp && adduser -S -G cmp cmp \
    && mkdir -p /data /config && chown -R cmp:cmp /data /config
USER cmp
