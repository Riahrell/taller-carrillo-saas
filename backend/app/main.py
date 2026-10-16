"""Application factory. Business routes arrive only after access controls."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, load_settings
from app.core.database import build_engine, create_session_factory, verify_connection


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings if settings is not None else load_settings()

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
