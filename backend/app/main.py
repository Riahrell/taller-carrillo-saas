"""Application factory. Business routes arrive only after access controls."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, load_settings
from app.core.database import build_engine, create_session_factory, verify_connection
from app.modules.access.http_middleware import HttpSecurityMiddleware
from app.modules.access.http_security import HttpSecurity


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings if settings is not None else load_settings()
    http_security = HttpSecurity(configuration)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(configuration)
        try:
            await run_in_threadpool(verify_connection, engine)
            app.state.session_factory = create_session_factory(engine)
            yield
        finally:
            await run_in_threadpool(engine.dispose)

    application = FastAPI(
        title="Taller Carrillo API",
        version="0.1.0",
        debug=False,
        lifespan=lifespan,
        docs_url="/docs" if configuration.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if configuration.docs_enabled else None,
    )
    application.state.settings = configuration
    application.state.http_security = http_security
    application.add_middleware(HttpSecurityMiddleware, policy=http_security)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            {"detail": "Invalid request"},
            status_code=422,
            headers={"Cache-Control": "no-store"},
        )

    @application.get(
        "/health/live",
        response_model=LivenessResponse,
        include_in_schema=False,
        name="liveness",
    )
    async def liveness() -> LivenessResponse:
        # Explicitly public. Liveness is not a database/readiness check.
        return LivenessResponse()

    return application
