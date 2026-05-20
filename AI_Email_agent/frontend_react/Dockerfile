# ── Stage 1: build ───────────────────────────────────────────────────────────
FROM node:22-alpine AS build

WORKDIR /app

# Install dependencies before copying source so Docker layer-caching works
COPY package*.json ./
RUN npm ci --frozen-lockfile

COPY . .
RUN npm run build

# ── Stage 2: serve ────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# Copy built SPA
COPY --from=build /app/dist /usr/share/nginx/html

# Replace default nginx config with our SPA + API proxy config
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost:3000/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
