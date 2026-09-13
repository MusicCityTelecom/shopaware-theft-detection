FROM node:24-alpine
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN corepack pnpm install --frozen-lockfile
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack pnpm build
EXPOSE 3000
CMD ["corepack", "pnpm", "start", "--hostname", "0.0.0.0"]
