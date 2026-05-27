"""
ZeroChainAI — Authentication & Request Verification
API Key + HMAC signature + Vault integration.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

logger = logging.getLogger("zeroscan.auth")

# ── API Key header ───────────────────────────────────────────
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ── Secrets (loaded from Vault or env fallback for dev) ─────
def _load_valid_keys() -> set[str]:
    """
    Load valid API keys from HashiCorp Vault.
    Falls back to ZEROSCAN_KEYS env variable for local dev.
    In production VAULT_TOKEN must be set.
    """
    vault_token = os.getenv("VAULT_TOKEN")
    if vault_token:
        try:
            import hvac
            client = hvac.Client(
                url=os.getenv("VAULT_ADDR", "http://127.0.0.1:8200"),
                token=vault_token,
            )
            secret = client.secrets.kv.v2.read_secret_version(
                path="zeroscan", mount_point="secret"
            )
            keys_str = secret["data"]["data"].get("api_keys", "")
            return set(k.strip() for k in keys_str.split(",") if k.strip())
        except Exception as e:
            logger.error(f"Vault read failed: {e}. Falling back to env.")

    # Dev fallback — comma-separated keys in env
    keys_str = os.getenv("ZEROSCAN_KEYS", os.getenv("ZEROSCAN_API_KEY", ""))
    keys = set(k.strip() for k in keys_str.split(",") if k.strip())
    if not keys:
        raise RuntimeError("No API keys configured. Set ZEROSCAN_KEYS or use Vault.")
    return keys


def _get_hmac_secret() -> bytes:
    """Get HMAC signing secret from Vault or env."""
    vault_token = os.getenv("VAULT_TOKEN")
    if vault_token:
        try:
            import hvac
            client = hvac.Client(
                url=os.getenv("VAULT_ADDR", "http://127.0.0.1:8200"),
                token=vault_token,
            )
            secret = client.secrets.kv.v2.read_secret_version(
                path="zeroscan", mount_point="secret"
            )
            return secret["data"]["data"]["hmac_secret"].encode()
        except Exception:
            pass
    return os.getenv("ZEROSCAN_HMAC_SECRET", "dev-only-secret-change-in-prod").encode()


# Cache at startup (reload on SIGHUP for key rotation without downtime)
_VALID_KEYS: set[str] = set()
_HMAC_SECRET: bytes = b""


def init_auth():
    """Call at application startup."""
    global _VALID_KEYS, _HMAC_SECRET
    _VALID_KEYS = _load_valid_keys()
    _HMAC_SECRET = _get_hmac_secret()
    logger.info(f"Auth initialized: {len(_VALID_KEYS)} API key(s) loaded")


# ── Key verification ─────────────────────────────────────────
def _verify_api_key(key: Optional[str]) -> bool:
    if not key or not _VALID_KEYS:
        return False
    # Constant-time comparison to prevent timing attacks
    return any(hmac.compare_digest(key, valid) for valid in _VALID_KEYS)


def _get_key_id(key: str) -> str:
    """Anonymized key identifier for logging (first 8 chars + hash)."""
    return key[:8] + "..." + hashlib.sha256(key.encode()).hexdigest()[:8]


# ── HMAC Request Signing ─────────────────────────────────────
def _verify_hmac(timestamp: str, body: bytes, signature: str) -> bool:
    """
    Verify HMAC-SHA256 signature: HMAC(secret, f"{timestamp}.{body}")
    Prevents replay attacks and request tampering.
    """
    if not _HMAC_SECRET:
        return True  # dev mode without HMAC
    try:
        message = f"{timestamp}.".encode() + body
        expected = hmac.new(_HMAC_SECRET, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


# ── FastAPI dependency ───────────────────────────────────────
async def require_auth(
    request: Request,
    api_key: Optional[str] = Security(_api_key_header),
) -> str:
    """
    FastAPI dependency — validates API key + optional HMAC signature.
    Returns the key_id for logging.
    Usage: @app.post("/scan")
           async def scan(key_id: str = Depends(require_auth)):
    """
    # 1. Check API key presence
    if not api_key:
        logger.warning(f"Missing API key from {request.client.host}")
        raise HTTPException(401, detail="Missing X-API-Key header")

    # 2. Validate API key (constant-time)
    if not _verify_api_key(api_key):
        logger.warning(f"Invalid API key: {api_key[:8]}... from {request.client.host}")
        raise HTTPException(403, detail="Invalid API key")

    # 3. HMAC signature (optional but logged)
    timestamp = request.headers.get("X-Timestamp", "")
    signature = request.headers.get("X-Signature", "")

    if timestamp and signature:
        # Replay attack protection: reject if timestamp is > 5 min old
        try:
            ts = int(timestamp)
            if abs(time.time() - ts) > 300:
                raise HTTPException(403, detail="Request expired (clock skew > 5 min)")
        except (ValueError, TypeError):
            raise HTTPException(400, detail="Invalid X-Timestamp")

        body = await request.body()
        if not _verify_hmac(timestamp, body, signature):
            logger.warning(f"HMAC verification failed from {request.client.host}")
            raise HTTPException(403, detail="Invalid request signature")

    key_id = _get_key_id(api_key)
    logger.debug(f"Auth OK: key={key_id} ip={request.client.host}")
    return key_id
