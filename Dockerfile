# syntax=docker/dockerfile:1.7

FROM python:3.13.10-slim-bookworm AS python-builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY pyproject.toml README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.13.10-slim-bookworm AS python-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/hmt-crypto/bin:$PATH

RUN groupadd --system --gid 10001 hmt \
    && useradd --system --uid 10001 --gid hmt --home-dir /nonexistent --shell /usr/sbin/nologin hmt \
    && python -m venv /opt/hmt-crypto

COPY --from=python-builder /wheels /wheels
RUN /opt/hmt-crypto/bin/python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

WORKDIR /app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
USER 10001:10001

FROM python-runtime AS migrate
CMD ["alembic", "upgrade", "head"]

FROM python-runtime AS api
EXPOSE 8000
CMD ["hmt-crypto-api"]

FROM python-runtime AS worker
CMD ["hmt-crypto-worker"]

FROM node:22.21.1-bookworm-slim AS ui-builder
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --ignore-scripts
COPY ui ./
RUN npm run build

FROM nginx:1.28.0-alpine AS ui
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=ui-builder /ui/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080
