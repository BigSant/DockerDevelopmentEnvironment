ARG PLAYWRIGHT_VERSION

FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-jammy AS base

ARG PLAYWRIGHT_VERSION

WORKDIR /e2e
RUN npm init -y >/dev/null 2>&1 \
 && npm install @playwright/test@${PLAYWRIGHT_VERSION}

ENV PATH=/e2e/node_modules/.bin:$PATH

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
