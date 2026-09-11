# syntax=docker/dockerfile:1.7
# ---- deps -------------------------------------------------------------------
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# ---- build ------------------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# NEXT_PUBLIC_* values are inlined into the client bundle at build time.
ARG NEXT_PUBLIC_AEGIS_API_URL=http://localhost:8600
ARG NEXT_PUBLIC_AEGIS_API_KEY=
ARG NEXT_PUBLIC_TEMPORAL_UI_URL=http://localhost:8233
ARG NEXT_PUBLIC_GRAFANA_URL=http://localhost:3000
ARG NEXT_PUBLIC_PROMETHEUS_URL=http://localhost:9090
ENV NEXT_PUBLIC_AEGIS_API_URL=$NEXT_PUBLIC_AEGIS_API_URL \
    NEXT_PUBLIC_AEGIS_API_KEY=$NEXT_PUBLIC_AEGIS_API_KEY \
    NEXT_PUBLIC_TEMPORAL_UI_URL=$NEXT_PUBLIC_TEMPORAL_UI_URL \
    NEXT_PUBLIC_GRAFANA_URL=$NEXT_PUBLIC_GRAFANA_URL \
    NEXT_PUBLIC_PROMETHEUS_URL=$NEXT_PUBLIC_PROMETHEUS_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- runner -----------------------------------------------------------------
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S -g 1001 aegis && adduser -S -u 1001 -G aegis aegis
COPY --from=build --chown=aegis:aegis /app/public ./public
COPY --from=build --chown=aegis:aegis /app/.next/standalone ./
COPY --from=build --chown=aegis:aegis /app/.next/static ./.next/static
USER aegis
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:3000/ >/dev/null || exit 1
CMD ["node", "server.js"]
