FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime Image ──────────────────────────────────────────
FROM python:3.12-slim

# Security: non-root user
RUN groupadd -r zeroscan && useradd -r -g zeroscan -s /bin/false zeroscan

# Slither dependencies
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    git curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# App directory with correct ownership
RUN mkdir -p /app /data /var/log/zeroscan && \
    chown -R zeroscan:zeroscan /app /data /var/log/zeroscan

WORKDIR /app
COPY --chown=zeroscan:zeroscan . .

# Switch to non-root
USER zeroscan

# Expose only localhost (WireGuard VPN provides the tunnel)
EXPOSE 8443

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://127.0.0.1:8443/health || exit 1

CMD ["uvicorn", "src.api.main:app", \
     "--host", "127.0.0.1", \
     "--port", "8443", \
     "--workers", "2", \
     "--log-level", "info", \
     "--access-log"]
