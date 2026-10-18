# Bifrost Client - Production Container Image
# Multi-stage build for optimized production image

# =============================================================================
# Stage 1: Base with dependencies
# Uses BUILDPLATFORM to run on native architecture (no QEMU emulation)
# =============================================================================
# node:20-slim
FROM --platform=$BUILDPLATFORM node:26-slim@sha256:14bf3eac4bf209d906d3c41256597d3ab1f926b2e93a79e9bdfe1efd32454239 AS base

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci

# =============================================================================
# Stage 2: Build for production
# =============================================================================
FROM base AS builder

# Copy source code
COPY . .

# Build the application
# Increase Node memory limit to prevent OOM during build
ENV NODE_OPTIONS="--max-old-space-size=4096"
ARG VITE_BIFROST_VERSION=unknown
ENV VITE_BIFROST_VERSION=${VITE_BIFROST_VERSION}
RUN npm run build \
    && printf '%s\n' "$VITE_BIFROST_VERSION" > dist/bifrost-version.txt \
    && find dist -type d -exec chmod 755 {} + \
    && find dist -type f -exec chmod 644 {} +

# Local/CI verification target: builder already type-checks and bundles.
# Run the remaining Node checks required by the merge gate once.
FROM builder AS ci

CMD ["sh", "-c", "npm run lint && npm test"]

# =============================================================================
# Stage 3: Production with nginx
# =============================================================================
# nginx:alpine
FROM nginx:alpine@sha256:72ba65eb42c10344912a84ff42408db7d34f2feb642204570ab8fc5ffd29f1d3 AS production

# Copy built assets from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# The official nginx entrypoint renders this template before starting nginx.
# Restrict substitution so nginx variables ($host, $uri, captures) stay intact.
ENV BIFROST_API_UPSTREAM=api:8000 \
    BIFROST_RENDERER_UPSTREAM=127.0.0.1:8080 \
    BIFROST_RENDERER_RESOLVER=127.0.0.11 \
    BIFROST_CANONICAL_SCHEME=http \
    NGINX_ENVSUBST_FILTER=^(BIFROST_API_UPSTREAM|BIFROST_RENDERER_UPSTREAM|BIFROST_RENDERER_RESOLVER|BIFROST_CANONICAL_SCHEME)$
COPY nginx.conf /etc/nginx/templates/default.conf.template

RUN chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx /etc/nginx/conf.d \
    && touch /var/run/nginx.pid \
    && chown nginx:nginx /var/run/nginx.pid

USER nginx

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

