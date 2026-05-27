"""
ZeroChainAI — FastAPI Main Application
Security-hardened orchestrator API.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .auth import init_auth, require_auth
from .cache import ScanCache
from .ensemble.router import run_ensemble
from .models import ScanRequest, ScanReport

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)s | %(message)s",
)
logger         = logging.getLogger("zeroscan.api")
security_logger = logging.getLogger("zeroscan.security")

# ── Rate Limiting ────────────────────────────────────────────
def _get_key_for_limit(request: Request) -> str:
    """Rate limit by API key, not IP (keys come through VPN)."""
    return request.headers.get("X-API-Key", get_remote_address(request))

limiter = Limiter(key_func=_get_key_for_limit)

# ── App ──────────────────────────────────────────────────────
app = FastAPI(
    title="ZeroChainAI Orchestrator",
    version="1.0.0",
    docs_url=None,    # Disable Swagger UI in production
    redoc_url=None,   # Disable ReDoc in production
    openapi_url=None, # Disable OpenAPI schema in production
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Only accept requests from VPN subnet
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["10.99.0.1", "localhost", "127.0.0.1"],
)

# ── Cache ────────────────────────────────────────────────────
cache = ScanCache(db_path=os.getenv("CACHE_DB", "/data/zeroscan_cache.db"))


# ── Security Middleware ──────────────────────────────────────
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Log all requests with security context."""
    start = time.perf_counter()

    # Security: reject requests not from VPN
    client_ip = request.client.host if request.client else "unknown"
    if client_ip not in ("10.99.0.2", "127.0.0.1", "::1"):
        security_logger.warning(
            f"REQUEST_FROM_OUTSIDE_VPN ip={client_ip} path={request.url.path}"
        )
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    response = await call_next(request)
    duration = int((time.perf_counter() - start) * 1000)

    security_logger.info(json.dumps({
        "time":     datetime.now(timezone.utc).isoformat(),
        "ip":       client_ip,
        "method":   request.method,
        "path":     request.url.path,
        "status":   response.status_code,
        "duration": duration,
        "key_id":   request.headers.get("X-API-Key", "")[:8] + "...",
    }))

    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["X-XSS-Protection"]       = "1; mode=block"
    response.headers["Cache-Control"]          = "no-store"

    return response


# ── Startup ──────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    init_auth()
    await cache.init()
    logger.info("🛡️ ZeroChainAI Orchestrator started")
    logger.info(f"   Listening on localhost only (VPN access required)")


# ── Health Check (unauthenticated — for Docker healthcheck) ─
@app.get("/health")
async def health():
    return {"status": "ok", "service": "zeroscan-orchestrator", "version": "1.0.0"}


# ── Main Scan Endpoint ───────────────────────────────────────
@app.post("/scan", response_model=ScanReport)
@limiter.limit("10/hour")    # 10 scans per hour per API key
async def scan(
    request: Request,
    body: ScanRequest,
    key_id: str = Depends(require_auth),
):
    """
    Analyze a smart contract using the AI ensemble.

    - Runs Claude Opus 4 + Gemini 2.5 Pro + OpenAI o3 + Slither in parallel
    - Returns aggregated findings with consensus voting
    - Results cached for 24h by contract hash
    """
    # Compute contract hash for caching
    code_hash = hashlib.sha256(body.code.encode()).hexdigest()

    # Check cache (unless force_rescan requested)
    if not body.force_rescan:
        cached = await cache.get(code_hash)
        if cached:
            cached.from_cache = True
            logger.info(f"Cache hit: hash={code_hash[:16]}...")
            return cached

    # Run ensemble
    logger.info(f"Scan started: key={key_id} hash={code_hash[:16]}... lang={body.language}")
    try:
        report = await run_ensemble(body)
    except Exception as e:
        logger.exception(f"Ensemble failed: {e}")
        raise HTTPException(500, detail="Scan failed. Please try again.")

    # Cache result
    await cache.set(code_hash, report)

    return report


# ── Scan History ─────────────────────────────────────────────
@app.get("/history")
@limiter.limit("30/hour")
async def history(
    request: Request,
    limit: int = 20,
    key_id: str = Depends(require_auth),
):
    """Get recent scan history."""
    scans = await cache.list_recent(limit=min(limit, 100))
    return {"scans": scans, "total": len(scans)}


# ── Status ───────────────────────────────────────────────────
@app.get("/status")
@limiter.limit("60/hour")
async def status(
    request: Request,
    key_id: str = Depends(require_auth),
):
    """Service status and agent availability."""
    stats = await cache.stats()
    return {
        "status":  "operational",
        "version": "1.0.0",
        "agents":  ["claude-opus-4", "gemini-2.5-pro", "openai-o3", "slither"],
        "cache":   stats,
    }
