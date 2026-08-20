"""CipherContact application entry point.

Steps 1-3 expose only health endpoints. Authentication, authorization, and business
routes are added in later Phase 1 steps.
"""

from __future__ import annotations

from fastapi import FastAPI, Response
from sqlalchemy import text

from app.api.admin import router as admin_router
from app.auth.router import router as auth_router
from app.config import get_settings
from app.db import engine

app = FastAPI(
    title="CipherContact",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.include_router(auth_router)
app.include_router(admin_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up and serving."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz(response: Response) -> dict[str, str]:
    """Readiness: required dependencies respond. Returns 503 if any check fails."""
    checks: dict[str, str] = {}
    healthy = True

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
        healthy = False

    try:
        import redis

        client = redis.Redis.from_url(get_settings().redis_url)
        client.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"
        healthy = False

    if not healthy:
        response.status_code = 503
    checks["status"] = "ok" if healthy else "degraded"
    return checks
