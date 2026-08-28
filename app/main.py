"""CipherContact application entry point."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.admin import router as admin_router
from app.api.campaigns import campaigns_router, imports_router
from app.api.flags import router as flags_router
from app.api.work import agent_router, work_router
from app.api.workforce import router as workforce_router
from app.auth.router import router as auth_router
from app.config import get_settings
from app.db import engine
from app.logging_setup import configure_logging
from app.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.web.agent_work import router as web_agent_work_router
from app.web.audit import router as web_audit_router
from app.web.auth_pages import router as web_auth_router
from app.web.campaigns import router as web_campaigns_router
from app.web.dashboard import router as web_dashboard_router
from app.web.dependencies import InvalidFormCsrf, RedirectToLogin
from app.web.flags import router as web_flags_router
from app.web.workforce import router as web_workforce_router

configure_logging(get_settings().log_level)

app = FastAPI(
    title="CipherContact",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)

app.mount(
    "/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static"
)


@app.exception_handler(RedirectToLogin)
async def _redirect_to_login(request: Request, exc: RedirectToLogin) -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(InvalidFormCsrf)
async def _invalid_form_csrf(request: Request, exc: InvalidFormCsrf) -> RedirectResponse:
    if request.url.path.startswith("/agent/work"):
        target = "/agent/work"
    elif request.url.path.startswith("/campaigns"):
        target = "/campaigns"
    else:
        target = "/dashboard"
    return RedirectResponse(
        target + "?flash_error=Your+session+expired+or+the+form+was+stale.+Please+retry.",
        status_code=303,
    )


app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(campaigns_router)
app.include_router(imports_router)
app.include_router(work_router)
app.include_router(agent_router)
app.include_router(workforce_router)
app.include_router(flags_router)
app.include_router(web_auth_router)
app.include_router(web_agent_work_router)
app.include_router(web_campaigns_router)
app.include_router(web_workforce_router)
app.include_router(web_audit_router)
app.include_router(web_flags_router)
app.include_router(web_dashboard_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up and serving. Public and minimal."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, str]:
    """Readiness with dependency checks. Gated by a health token when one is configured."""
    settings = get_settings()
    token = settings.health_token.get_secret_value()
    if token and request.headers.get("x-health-token") != token:
        # Hide the endpoint's existence from untrusted callers.
        response.status_code = 404
        return {"status": "not found"}

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

        redis.Redis.from_url(settings.redis_url).ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"
        healthy = False

    if not healthy:
        response.status_code = 503
    checks["status"] = "ok" if healthy else "degraded"
    return checks
