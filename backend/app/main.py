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
from app.modules.access.administration import (
    AdministrationDenied,
    AdministrationUnavailable,
    LastAdministratorRequired,
    MembershipAdministrator,
    MembershipStateChanged,
    UserCreationConflict,
)
from app.modules.access.administration_routes import router as administration_router
from app.modules.access.authentication import AuthenticationUnavailable
from app.modules.access.http_middleware import HttpSecurityMiddleware
from app.modules.access.http_security import HttpSecurity, RequestRejected
from app.modules.access.http_service import HttpAccess
from app.modules.access.identity_http import IdentityAccess
from app.modules.access.identity_routes import router as identity_router
from app.modules.access.membership import AccessDenied
from app.modules.access.reauthentication import RecentAuthenticationRequired
from app.modules.access.routes import router as access_router
from app.modules.access.trusted_proxy import TrustedProxy
from app.modules.catalog.routes import router as catalog_router
from app.modules.catalog.service import (
    CatalogAdministrator,
    CatalogConflict,
    CatalogDenied,
    CatalogUnavailable,
)
from app.modules.inventory.routes import router as inventory_router
from app.modules.inventory.service import (
    InventoryAdministrator,
    InventoryConflict,
    InventoryDenied,
    InventoryInvalid,
    InventoryUnavailable,
)


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings if settings is not None else load_settings()
    http_security = HttpSecurity(configuration)
    trusted_proxy = TrustedProxy(configuration)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(configuration)
        try:
            from app.modules.access.password_policy import _common

            await run_in_threadpool(_common)
            await run_in_threadpool(verify_connection, engine)
            app.state.session_factory = create_session_factory(engine)
            app.state.access = await run_in_threadpool(HttpAccess, engine)
            app.state.membership_administrator = MembershipAdministrator(engine)
            app.state.catalog = CatalogAdministrator(engine)
            app.state.inventory = InventoryAdministrator(engine)
            app.state.identity = IdentityAccess(
                engine, configuration.identity_keys_file
            )
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
    application.state.trusted_proxy = trusted_proxy
    application.include_router(access_router)
    application.include_router(identity_router)
    application.include_router(administration_router)
    application.include_router(catalog_router)
    application.include_router(inventory_router)
    application.add_middleware(HttpSecurityMiddleware, policy=http_security)

    def domain_error(status: int, detail: str) -> JSONResponse:
        return JSONResponse(
            {"detail": detail},
            status_code=status,
            headers={"Cache-Control": "no-store"},
        )

    @application.exception_handler(CatalogDenied)
    async def catalog_denied(_request: Request, _error: CatalogDenied):
        return domain_error(403, "Access denied")

    @application.exception_handler(InventoryDenied)
    async def inventory_denied(_request: Request, _error: InventoryDenied):
        return domain_error(403, "Access denied")

    @application.exception_handler(InventoryConflict)
    async def inventory_conflict(_request: Request, _error: InventoryConflict):
        return domain_error(409, "Operation key conflict")

    @application.exception_handler(InventoryInvalid)
    async def inventory_invalid(_request: Request, _error: InventoryInvalid):
        return domain_error(422, "Invalid physical count")

    @application.exception_handler(InventoryUnavailable)
    async def inventory_unavailable(_request: Request, _error: InventoryUnavailable):
        return domain_error(503, "Inventory unavailable")

    @application.exception_handler(CatalogConflict)
    async def catalog_conflict(_request: Request, _error: CatalogConflict):
        return domain_error(409, "SKU unavailable")

    @application.exception_handler(CatalogUnavailable)
    async def catalog_unavailable(_request: Request, _error: CatalogUnavailable):
        return domain_error(503, "Catalog unavailable")

    @application.exception_handler(AdministrationDenied)
    async def administration_denied(_request: Request, _error: AdministrationDenied):
        return domain_error(403, "Access denied")

    @application.exception_handler(AccessDenied)
    async def unauthenticated(_request: Request, _error: AccessDenied):
        return domain_error(401, "Access denied")

    @application.exception_handler(RecentAuthenticationRequired)
    async def recent_required(_request: Request, _error: RecentAuthenticationRequired):
        return domain_error(428, "Recent authentication required")

    @application.exception_handler(LastAdministratorRequired)
    async def last_administrator(_request: Request, _error: LastAdministratorRequired):
        return domain_error(409, "An active administrator is required")

    @application.exception_handler(MembershipStateChanged)
    async def stale_membership(_request: Request, _error: MembershipStateChanged):
        return domain_error(412, "Membership state changed")

    @application.exception_handler(UserCreationConflict)
    async def user_creation_conflict(_request: Request, _error: UserCreationConflict):
        return domain_error(409, "User creation unavailable")

    @application.exception_handler(AdministrationUnavailable)
    async def administration_unavailable(
        _request: Request, _error: AdministrationUnavailable
    ):
        # Same access error as the existing Nginx /api/auth/ failure normalizer.
        return domain_error(503, "Authentication unavailable")

    @application.exception_handler(RequestRejected)
    async def rejected(_request: Request, _error: RequestRejected) -> JSONResponse:
        return JSONResponse(
            {"detail": "Request rejected"},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )

    @application.exception_handler(AuthenticationUnavailable)
    async def unavailable(
        _request: Request, _error: AuthenticationUnavailable
    ) -> JSONResponse:
        return JSONResponse(
            {"detail": "Authentication unavailable"},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    from app.modules.access.enrollment_email import EnrollmentEmailUnavailable
    from app.modules.access.mailbox_service import MailboxUnavailable
    from app.modules.access.mfa_enrollment import MfaEnrollmentUnavailable
    from app.modules.access.mfa_recovery import MfaRecoveryUnavailable
    from app.modules.access.mfa_regeneration import MfaRegenerationUnavailable
    from app.modules.access.mfa_replacement import MfaReplacementUnavailable
    from app.modules.access.mfa_rotation import MfaRotationUnavailable
    from app.modules.access.mfa_sessions import MfaSessionUnavailable
    from app.modules.access.mfa_verification import MfaVerificationUnavailable
    from app.modules.access.password_reset import PasswordResetUnavailable

    async def identity_unavailable(_request: Request, _error: Exception):
        return domain_error(503, "Authentication unavailable")

    for error in (
        MfaRotationUnavailable,
        PasswordResetUnavailable,
        EnrollmentEmailUnavailable,
        MailboxUnavailable,
        MfaEnrollmentUnavailable,
        MfaReplacementUnavailable,
        MfaRegenerationUnavailable,
        MfaRecoveryUnavailable,
        MfaSessionUnavailable,
        MfaVerificationUnavailable,
    ):
        application.add_exception_handler(error, identity_unavailable)

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
