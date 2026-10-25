# RootCause web (Next.js) image: `web` service of deploy/docker-compose.yml.
#
#   docker build -t rootcause-web:dev web/
#
# NEXT_PUBLIC_* values are inlined into the browser bundle at BUILD time, so
# they are build args (compose passes them under build.args), not runtime
# env. They are public URLs, never secrets: anything NEXT_PUBLIC_* ends up in
# JavaScript every visitor can read.
#
# Multi-stage: `npm ci` from package-lock.json, `next build` with
# output: "standalone" (web/next.config.ts), then a runtime stage holding only
# the standalone server and static assets, run as the image's unprivileged
# `node` user.
#
# Base image: exact tag plus the multi-arch index digest observed 2026-10-25
# (package.json engines: node >= 22). Bump in a `chore(deploy):` commit.

FROM node:22.23.4-alpine3.23@sha256:6abbcf5dd6b231da109bcde1181db1d92bf78015ae1a9514d9807c1f857869db AS deps
WORKDIR /app
# .npmrc carries legacy-peer-deps=true, which the lockfile was resolved with.
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund

FROM node:22.23.4-alpine3.23@sha256:6abbcf5dd6b231da109bcde1181db1d92bf78015ae1a9514d9807c1f857869db AS builder
WORKDIR /app

# Browser-facing service URLs (the browser calls api/agent directly on the
# host's published ports; there is no server-side proxy in the MVP).
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_AGENT_URL=http://localhost:8100
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_AGENT_URL=${NEXT_PUBLIC_AGENT_URL} \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22.23.4-alpine3.23@sha256:6abbcf5dd6b231da109bcde1181db1d92bf78015ae1a9514d9807c1f857869db AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Root-owned, read-only application files; only Next's cache dir is writable.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
RUN mkdir -p .next/cache && chown node:node .next/cache

USER node
EXPOSE 3000
CMD ["node", "server.js"]
