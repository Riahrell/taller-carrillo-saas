"""Thin HTTP adapter for existing memberships; no registration or tenant input."""

import re
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from app.modules.access.administration import MembershipChange, MembershipView
from app.modules.access.membership import AccessDenied

router = APIRouter(prefix="/auth/memberships", tags=["membership administration"])
_ETAG = re.compile(r'"membership-v1-[0-9a-f]{64}"', re.ASCII)
_NO_STORE = {"Cache-Control": "no-store"}


def _token(request: Request) -> SecretStr:
    if request.query_params:
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


def _representation(view: MembershipView) -> JSONResponse:
    return JSONResponse(
        view.model_dump(mode="json"), headers=_NO_STORE | {"ETag": view.etag}
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
        token, membership_id=membership_id, change=payload, expected_etag=expected
    )
    response = _representation(
        MembershipView(
            membership_id=result.membership_id,
            role=result.role,
            is_active=result.is_active,
        )
    )
    if result.session_revoked:
        request.app.state.http_security.clear_cookies(response)
    return response
