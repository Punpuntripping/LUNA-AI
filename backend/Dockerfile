# Luna Legal AI — Backend (multi-stage, non-root)
# Build context: repo root (needs shared/ and agents/ siblings)

FROM python:3.11-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
# Install from the PINNED lockfile, NOT requirements.txt, so every rebuild is
# byte-identical (a routine rebuild auto-upgrading an unpinned dep — fastapi
# 0.137.0 — caused the 2026-06-15 login outage). requirements.txt holds version
# *intent* (ranges + hard pins); requirements.lock is the resolved, verified set.
# Regenerate the lock after editing requirements.txt — see requirements.lock header.
COPY backend/requirements.txt backend/requirements.lock ./
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.lock

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY shared/ /app/shared/
COPY agents/ /app/agents/
COPY backend/ /app/backend/
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --ingroup appgroup appuser
USER appuser
WORKDIR /app/backend
ENV PORT=8000
EXPOSE ${PORT}
# Listen on BOTH stacks. Railway's public edge proxy reaches the container over
# IPv4 (the old `0.0.0.0` bind), but private networking — `luna-backend.railway.
# internal`, which the frontend's server-side ISR fetches now use — is IPv6-only
# on legacy environments, so an IPv4-only socket resolves and then refuses every
# private connection.
# The empty host is what actually binds both: asyncio's `create_server` maps
# `host == ""` to "all interfaces" and opens one socket PER family
# (0.0.0.0 + [::]). ⚠ Do NOT "simplify" this to `--host ::` — CPython sets
# IPV6_V6ONLY=1 on every AF_INET6 listener it creates, so `::` alone is
# IPv6-ONLY and the public domain would start 502'ing "Application failed to
# respond" while looking correct in review.
CMD uvicorn app.main:app --host "" --port ${PORT}
