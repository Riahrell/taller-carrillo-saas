"""Bound employee registration and separate administrator approval."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import Field, SecretStr

from app.modules.access.administration import MembershipListQuery
from app.modules.access.administration_routes import _token
from app.modules.access.employee_registration import (
    RegistrationDenied,
    RegistrationInput,
    RegistrationRate,
)
from app.modules.access.identity_http import browser_binding
from app.modules.access.invitation_routes import actor, response
from app.modules.access.routes import EmptyInput

router = APIRouter(prefix="/auth", tags=["employee registration"])
NO_STORE = {"Cache-Control": "no-store"}


class RegistrationCode(EmptyInput):
    code: SecretStr = Field(min_length=1, max_length=128, repr=False)


def service(request):
    return request.app.state.identity.require().employee_registration


def bound(request):
    if request.query_params:
        raise RegistrationDenied()
    policy = request.app.state.http_security
    policy.login_binding(request.headers)  # Registration never replaces a live session.
    raw = policy.read_cookies(request.headers).get(policy.registration_cookie)
    if raw is None:
        raise RegistrationDenied()
    return SecretStr(raw), browser_binding(policy, request.headers)


def public(method):
    from functools import wraps

    @wraps(method)
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except RegistrationRate:
            raise HTTPException(
                429,
                "Registration temporarily unavailable",
                headers=NO_STORE | {"Retry-After": "60"},
            ) from None
        except RegistrationDenied:
            raise HTTPException(
                400, "Registration unavailable", headers=NO_STORE
            ) from None

    return guarded


@router.post("/employee-registration", status_code=202)
@public
def start_registration(payload: RegistrationInput, request: Request):
    if request.query_params:
        raise RegistrationDenied()
    policy = request.app.state.http_security
    policy.login_binding(request.headers)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    with request.app.state.access.password_slot():
        token, expires = service(request).start(
            payload, browser_binding(policy, request.headers), peer
        )
    result = JSONResponse(
        {"status": "awaiting_code"}, status_code=202, headers=NO_STORE
    )
    result.set_cookie(
        policy.registration_cookie,
        token.get_secret_value(),
        expires=expires,
        secure=policy.secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return result


@router.get("/employee-registration/status")
@public
def registration_status(request: Request):
    request.app.state.http_security.verify_browser_read(request.headers)
    token, binding = bound(request)
    return JSONResponse(
        {"status": service(request).status(token, binding)}, headers=NO_STORE
    )


@router.post("/employee-registration/resend", status_code=204)
@public
def resend_registration(payload: EmptyInput, request: Request):
    token, binding = bound(request)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    service(request).resend(token, binding, peer)
    return Response(status_code=204, headers=NO_STORE)


@router.post("/employee-registration/confirm")
@public
def confirm_registration(payload: RegistrationCode, request: Request):
    token, binding = bound(request)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    if not service(request).confirm(token, binding, payload.code, peer):
        raise RegistrationDenied()
    return JSONResponse(
        {"status": service(request).status(token, binding)}, headers=NO_STORE
    )


@router.get("/employee-registrations")
def pending_registrations(
    request: Request, query: Annotated[MembershipListQuery, Query()]
):
    request.app.state.http_security.verify_browser_read(request.headers)
    # Listing works even when mail configuration is unavailable.
    registrations = request.app.state.employee_registrations
    items, after, bound_actor = registrations.pending(
        _token(request, pagination=True), query=query
    )
    return response({"items": items, "next_after": after}, bound_actor)


@router.post("/employee-registrations/{registration_id}/approve")
def approve_registration(registration_id: UUID, payload: EmptyInput, request: Request):
    registrations = request.app.state.employee_registrations
    membership, bound_actor = registrations.approve(
        _token(request), registration_id=registration_id, expected_actor=actor(request)
    )
    return response(
        {"membership_id": str(membership), "role": "read_only"}, bound_actor
    )
