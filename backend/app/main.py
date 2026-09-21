"""FastAPI application entrypoint.

Run from the repo root (so `agents`, `db`, `tools`, etc. resolve as top-level
packages exactly as they do for pytest/Streamlit today):

    uvicorn backend.app.main:app --reload --port 8000
"""

from __future__ import annotations

import inspect
import logging

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.api.deps import bind_usage_context
from backend.app.api.v1 import account as account_router
from backend.app.api.v1 import assistant as assistant_router
from backend.app.api.v1 import auth as auth_router
from backend.app.api.v1 import charts as charts_router
from backend.app.api.v1 import churn as churn_router
from backend.app.api.v1 import copilot as copilot_router
from backend.app.api.v1 import crm as crm_router
from backend.app.api.v1 import dashboard as dashboard_router
from backend.app.api.v1 import forecasting as forecasting_router
from backend.app.api.v1 import ingestion as ingestion_router
from backend.app.api.v1 import insights as insights_router
from backend.app.api.v1 import marketing as marketing_router
from backend.app.api.v1 import monitoring as monitoring_router
from backend.app.api.v1 import pipeline as pipeline_router
from backend.app.api.v1 import projects as projects_router
from backend.app.api.v1 import reports as reports_router
from backend.app.api.v1 import rootcause as rootcause_router
from backend.app.api.v1 import settings as settings_router
from backend.app.core.config import get_settings
from db.init_platform import ensure_platform_tables
from monitoring.scheduler import scheduler_enabled, shutdown_scheduler, start_scheduler

logging.basicConfig(level=logging.INFO)

# The usage binding is app-wide rather than per-router because every route that
# reaches an agent would otherwise need to remember it, and the one that forgets
# is the one whose tokens go unbilled. It no-ops on unauthenticated routes, so
# installing it here does not turn /auth/login into an authenticated endpoint.
app = FastAPI(
    title="DAAS API",
    version="0.1.0",
    dependencies=[Depends(bind_usage_context)],
)

settings = get_settings()
logger = logging.getLogger(__name__)


# Registered BEFORE CORSMiddleware, and that order is the whole point: Starlette
# applies the most recently added middleware outermost, so CORS ends up wrapping
# this handler. Without it, an unhandled exception is converted into a bare 500 by
# Starlette's own ServerErrorMiddleware, which sits *outside* CORS and therefore
# emits no CORS headers — the browser rejects that response and `fetch` rejects
# with the opaque "Failed to fetch", hiding the real cause (an HTTPException is
# unaffected: it's raised inside CORS and keeps its headers). Returning a response
# from here keeps the error inside CORS so the actual message reaches the UI.
@app.middleware("http")
async def unhandled_errors_as_json(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": f"{type(exc).__name__}: {exc}"},
        )


# Any loopback origin is this app's own dev UI, whatever port it landed on and
# whichever spelling of "this machine" the browser used. `[::1]` is in here because
# it is a *different origin string* from `localhost` even though it is the same
# machine: on a dual-stack Windows box the UI can end up addressed as
# http://[::1]:3000, and the origin is compared as text, so it was rejected while
# the identical page on http://localhost:3000 worked. That is one of the ways this
# check passed on one laptop and failed on another with the same code.
LOOPBACK_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?"

_cors_options: dict[str, object] = {
    "allow_origins": settings.frontend_origins,
    "allow_origin_regex": LOOPBACK_ORIGIN_REGEX,
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}

# Chrome's Private Network Access: when the browser decides a page is reaching from a
# less-private context into a loopback address, it adds
# `Access-Control-Request-Private-Network: true` to the preflight, and Starlette
# rejects the entire preflight with 400 unless the server opts in — even when origin,
# method and headers are all fine. Whether Chrome sends that header depends on the
# browser build and its policies rather than on this code, which is one way the same
# checkout passes every preflight on one laptop and fails every one on another.
#
# Feature-detected, not passed blindly: `allow_private_network` only exists in
# Starlette >= 0.51.0, and requirements.txt asks for `fastapi>=0.115.0`, which happily
# resolves to Starlette 0.37-0.50 on a machine that installed earlier. Passing it
# there raises TypeError at import and the API would not start at all — trading a CORS
# failure for a dead backend, on exactly the "it broke on the other device" path this
# is meant to close.
if "allow_private_network" in inspect.signature(CORSMiddleware).parameters:
    _cors_options["allow_private_network"] = True
else:
    logger.info(
        "Starlette here predates allow_private_network (needs >= 0.51.0); if a browser "
        "sends a Private Network Access preflight it will be refused. Upgrade with "
        "`pip install -U starlette` if preflights 400 with everything else correct."
    )

app.add_middleware(CORSMiddleware, **_cors_options)


class ExplainRejectedPreflight:
    """Logs *why* a CORS preflight was refused.

    A rejection is otherwise invisible: uvicorn's access log prints only
    "OPTIONS /api/v1/... 400 Bad Request", Starlette puts the reason in a response
    body nobody reads, and the browser — which never gets a valid preflight — reports
    it to the UI as a generic network failure. That combination is what turns a
    one-line config difference between two machines into a long hunt.

    Deliberately a raw ASGI middleware rather than ``@app.middleware("http")``:
    BaseHTTPMiddleware wraps every response, including the Server-Sent Events streams
    the copilot uses. This delegates untouched unless the request is a preflight, so
    nothing else in the app can be affected by it.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "OPTIONS":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start" and message["status"] == 400:
                headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
                logger.warning(
                    "CORS preflight REJECTED for %s from Origin %r. Allowed: %s, plus any "
                    "http(s) localhost / 127.0.0.1 / [::1] port. If the browser's address "
                    "bar shows a different host (a LAN IP, a tunnel), add that exact origin "
                    "to FRONTEND_ORIGIN in .env (comma-separated) and restart the API.",
                    scope.get("path"),
                    headers.get("origin"),
                    settings.frontend_origins or "(none configured)",
                )
            await send(message)

        await self.app(scope, receive, send_wrapper)


# Added last so it sits outside CORSMiddleware — the preflight is answered by CORS
# itself and never reaches the app, so it can only be observed from the outside.
app.add_middleware(ExplainRejectedPreflight)


@app.on_event("startup")
def _on_startup() -> None:
    try:
        ensure_platform_tables()
    except Exception:
        logging.getLogger(__name__).exception(
            "Could not reach Postgres on startup — is `docker compose up -d` running? "
            "The API will still start, but every data-backed endpoint will fail until it is."
        )

    # The monitoring scheduler is hosted in-process by default, so a standard
    # `uvicorn backend.app.main:app` is all a working deployment needs — no
    # broker, no second service to remember to start. Set MONITORING_SCHEDULER=0
    # to host it in `python -m monitoring.worker` instead. Running both is safe:
    # every run takes a Postgres advisory lock, so a duplicate is skipped rather
    # than delivered twice.
    if not scheduler_enabled():
        logger.info("Monitoring scheduler disabled here (MONITORING_SCHEDULER=0)")
        return
    try:
        start_scheduler()
    except Exception:
        logger.exception(
            "Could not start the monitoring scheduler. The API is unaffected, but scheduled "
            "briefings will not fire until this is resolved."
        )


@app.on_event("shutdown")
def _on_shutdown() -> None:
    try:
        shutdown_scheduler(wait=False)
    except Exception:  # pragma: no cover - best effort on the way out
        logger.warning("Monitoring scheduler did not shut down cleanly", exc_info=True)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router.router, prefix="/api/v1")
app.include_router(account_router.router, prefix="/api/v1")
app.include_router(projects_router.router, prefix="/api/v1")
app.include_router(ingestion_router.router, prefix="/api/v1")
app.include_router(ingestion_router.unscoped_router, prefix="/api/v1")
app.include_router(pipeline_router.router, prefix="/api/v1")
app.include_router(dashboard_router.router, prefix="/api/v1")
app.include_router(insights_router.router, prefix="/api/v1")
app.include_router(rootcause_router.router, prefix="/api/v1")
app.include_router(forecasting_router.router, prefix="/api/v1")
app.include_router(marketing_router.router, prefix="/api/v1")
app.include_router(churn_router.router, prefix="/api/v1")
app.include_router(crm_router.router, prefix="/api/v1")
app.include_router(copilot_router.router, prefix="/api/v1")
app.include_router(charts_router.router, prefix="/api/v1")
app.include_router(settings_router.router, prefix="/api/v1")
app.include_router(reports_router.router, prefix="/api/v1")
app.include_router(assistant_router.router, prefix="/api/v1")
app.include_router(monitoring_router.router, prefix="/api/v1")
