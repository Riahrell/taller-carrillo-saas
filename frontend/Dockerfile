FROM node:24-alpine@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f AS base
RUN apk add --no-cache 'zlib=1.3.2-r1'

FROM base AS dependencies
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit

FROM dependencies AS tools
COPY . .
CMD ["sh", "-c", "npm run lint && npm run format:check && npm run typecheck && npm test"]

FROM tools AS builder
RUN npm run build

FROM base AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
# Keep npm and its vendored dependencies out of the deployed runtime.
RUN node -e "const fs=require('node:fs'); for (const p of ['/usr/local/lib/node_modules/npm','/usr/local/bin/npm','/usr/local/bin/npx']) fs.rmSync(p,{recursive:true,force:true})"
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
USER node
CMD ["node", "server.js"]
