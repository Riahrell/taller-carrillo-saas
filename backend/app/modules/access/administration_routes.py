"""Session/admin membership adapter; no public registration or tenant input."""

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from app.modules.access.administration import (
    MembershipChange,
    MembershipListQuery,
    MembershipView,
    UserCreation,
)
from app.modules.access.http_security import RequestRejected
from app.modules.access.membership import AccessDenied

router = APIRouter(prefix="/auth/memberships", tags=["membership administration"])
_ETAG = re.compile(r'"membership-v1-[0-9a-f]{64}"', re.ASCII)
_NO_STORE = {"Cache-Control": "no-store"}


def _token(request: Request, *, pagination: bool = False) -> SecretStr:
    keys = [key for key, _ in request.query_params.multi_items()]
    if (not pagination and keys) or len(keys) != len(set(keys)):
        raise HTTPException(422, "Invalid request", headers=_NO_STORE)
    policy = request.app.state.http_security
    token = policy.read_cookies(request.headers).get(policy.session_cookie)
    if token is None:
        raise AccessDenied("Access denied")
    return SecretStr(token)


def _if_match(request: Request) -> str:
    values = request.headers.getlist("if-match")
    if not values:
        raise HTTPException(428, "Precondition required", headers=_NO_STORE)
    if len(values) != 1 or _ETAG.fullmatch(values[0]) is None:
        raise HTTPException(400, "Invalid precondition", headers=_NO_STORE)
    return values[0]


def _expected_actor(request: Request) -> UUID | None:
    values = request.headers.getlist("x-expected-membership")
    if not values:
        return None
    try:
        if len(values) != 1 or str(UUID(values[0])) != values[0]:
            raise ValueError
        return UUID(values[0])
    except ValueError:
        raise RequestRejected("Request rejected") from None


def _representation(view: MembershipView, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        view.model_dump(mode="json"),
        status_code=status_code,
        headers=_NO_STORE
        | {
            "ETag": view.etag,
            "X-Access-Membership": str(view.actor_membership_id),
        },
    )


@router.post("", status_code=201)
def create_user(payload: UserCreation, request: Request) -> JSONResponse:
    # Middleware validates origin/CSRF and bounds JSON before routing.
    token, expected = _token(request), _expected_actor(request)
    if expected is None:
        raise HTTPException(428, "Precondition required", headers=_NO_STORE)
    with request.app.state.access.password_slot():
        result = request.app.state.membership_administrator.create_user(
            token, data=payload, expected_actor=expected
        )
    response = _representation(result, status_code=201)
    # Resolve under both direct /auth/ and public /api/auth/, without trusting Host.
    response.headers["Location"] = f"./memberships/{result.membership_id}"
    return response


@router.get("")
def list_memberships(
    request: Request, query: Annotated[MembershipListQuery, Query()]
) -> JSONResponse:
    request.app.state.http_security.verify_browser_read(request.headers)
    result = request.app.state.membership_administrator.list_memberships(
        _token(request, pagination=True), query=query
    )
    return JSONResponse(
        result.model_dump(mode="json"),
        headers=_NO_STORE | {"X-Access-Membership": str(result.actor_membership_id)},
    )


@router.get("/{membership_id}")
def current_membership(membership_id: UUID, request: Request) -> JSONResponse:
    request.app.state.http_security.verify_browser_read(request.headers)
    result = request.app.state.membership_administrator.current_membership(
        _token(request), membership_id=membership_id
    )
    return _representation(result)


@router.patch("/{membership_id}")
def change_membership(
    membership_id: UUID, payload: MembershipChange, request: Request
) -> JSONResponse:
    # The global ASGI middleware checks origin, CSRF, JSON and size before routing.
    token, expected = _token(request), _if_match(request)
    result = request.app.state.membership_administrator.change_membership(
        token,
        membership_id=membership_id,
        change=payload,
        expected_etag=expected,
        expected_actor=_expected_actor(request),
    )
    response = _representation(
        MembershipView(
            membership_id=result.membership_id,
            role=result.role,
            is_active=result.is_active,
            actor_membership_id=result.actor_membership_id,
        )
    )
    if result.session_revoked:
        request.app.state.http_security.clear_cookies(response)
    return response
