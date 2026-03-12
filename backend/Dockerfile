# Luna Legal AI — Backend
# Multi-stage not needed; single slim stage keeps it simple and small.

FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# shared/ and agents/ live at /app alongside backend/
# Set PYTHONPATH so "from shared..." and "from agents..." resolve correctly
ENV PYTHONPATH=/app

WORKDIR /app

# Install dependencies first (layer cache — only invalidated when requirements change)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy the three source trees that the backend needs at runtime
COPY shared/ /app/shared/
COPY agents/ /app/agents/
COPY backend/ /app/backend/

# Runtime working directory — backend code uses relative paths from here
WORKDIR /app/backend

# Railway injects PORT at runtime; default to 8000 for local use
ENV PORT=8000

EXPOSE ${PORT}

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
