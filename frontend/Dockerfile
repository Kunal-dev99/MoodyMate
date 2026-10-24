# syntax=docker/dockerfile:1
# FE-34.1 / BE-34.2 (D-62 §5): the frontend image, built from frontend/ as the context.
#
#   docker build --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.test -t moodymate-frontend .
#
# NEXT_PUBLIC_API_BASE_URL is REQUIRED. Next inlines it into the client bundle at build time, so
# the image cannot be repointed at runtime; without it the build stops here (and FE-28.3's guard
# in next.config.mjs would stop `next build` too). It is a public origin, never a secret: build
# args are visible in `docker history`, so no secret may ever be passed as one (D-50 §2).
# OIDC_ISSUER (optional, public) adds the issuer to the CSP, as for a local build (FE-28.5).

ARG NODE_IMAGE=node:22-bookworm-slim

# ---- dependencies (locked: npm ci reads package-lock.json) ----
FROM ${NODE_IMAGE} AS deps
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# ---- build (output: 'standalone') ----
FROM ${NODE_IMAGE} AS build
ARG NEXT_PUBLIC_API_BASE_URL
ARG OIDC_ISSUER
RUN if [ -z "$(printf '%s' "${NEXT_PUBLIC_API_BASE_URL:-}" | tr -d '[:space:]')" ]; then \
      echo "FE-34.1: build arg NEXT_PUBLIC_API_BASE_URL is required, e.g." \
           "--build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.test" >&2; \
      exit 64; \
    fi
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    OIDC_ISSUER=${OIDC_ISSUER}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build && test -f .next/standalone/server.js

# ---- runtime: standalone server, non-root ----
FROM ${NODE_IMAGE} AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
# Application files stay root-owned and read-only to the runtime user; only Next's cache is
# writable.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
RUN mkdir -p .next/cache && chown 1000:1000 .next/cache
# uid/gid 1000 is the base image's `node` user; numeric so runAsNonRoot checks can verify it.
USER 1000:1000
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/login').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
CMD ["node", "server.js"]
