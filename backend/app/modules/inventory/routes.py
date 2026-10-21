"""Private stock HTTP; tenant, prior stock and deltas are never browser authority."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr

from app.modules.access.http_context import expected_membership
from app.modules.access.http_security import RequestRejected
from app.modules.access.membership import AccessDenied
from app.modules.inventory.inputs import (
    CountPageQuery,
    HistoryPageQuery,
    ManualStockInput,
    PhysicalCount,
)

router = APIRouter(prefix="/inventory", tags=["inventory administration"])
NO_STORE = {"Cache-Control": "no-store"}


def _token(request: Request, *, pagination: bool = False) -> SecretStr:
    request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    keys = [key for key, _ in request.query_params.multi_items()]
    if (not pagination and keys) or len(keys) != len(set(keys)):
        raise HTTPException(422, "Invalid request", headers=NO_STORE)
    policy = request.app.state.http_security
    cookies = policy.read_cookies(request.headers)
    if policy.preauth_cookie in cookies or policy.challenge_cookie in cookies:
        raise RequestRejected("Request rejected")
    token = cookies.get(policy.session_cookie)
    if token is None:
        raise AccessDenied("Access denied")
    return SecretStr(token)


def _response(result: BaseModel) -> JSONResponse:
    return JSONResponse(
        result.model_dump(mode="json"),
        headers=NO_STORE | {"X-Access-Membership": str(result.actor_membership_id)},
    )


def _operation_key(request: Request) -> UUID:
    values = request.headers.getlist("idempotency-key")
    if not values:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    try:
        if len(values) != 1 or str(UUID(values[0])) != values[0]:
            raise ValueError
        return UUID(values[0])
    except ValueError:
        raise HTTPException(422, "Invalid request", headers=NO_STORE) from None


@router.post("/counts")
def record_count(payload: PhysicalCount, request: Request):
    token = _token(request)
    expected = expected_membership(request)
    if expected is None:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    return _response(
        request.app.state.inventory.record_count(
            token,
            data=payload,
            operation_key=_operation_key(request),
            expected_actor=expected,
        )
    )


@router.get("/products/{product_id}/balance")
def current_balance(product_id: UUID, request: Request):
    request.app.state.http_security.verify_browser_read(request.headers)
    return _response(
        request.app.state.inventory.current_balance(
            _token(request),
            product_id=product_id,
            expected_actor=expected_membership(request),
        )
    )


@router.get("/products/{product_id}/counts")
def list_counts(
    product_id: UUID, request: Request, query: Annotated[CountPageQuery, Query()]
):
    request.app.state.http_security.verify_browser_read(request.headers)
    return _response(
        request.app.state.inventory.list_counts(
            _token(request, pagination=True),
            product_id=product_id,
            query=query,
            expected_actor=expected_membership(request),
        )
    )


@router.post("/movements")
def record_manual(payload: ManualStockInput, request: Request):
    token = _token(request)
    expected = expected_membership(request)
    if expected is None:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    return _response(
        request.app.state.inventory.record_manual(
            token,
            data=payload,
            operation_key=_operation_key(request),
            expected_actor=expected,
        )
    )


@router.get("/products/{product_id}/manual-movements")
def manual_history(
    product_id: UUID, request: Request, query: Annotated[CountPageQuery, Query()]
):
    request.app.state.http_security.verify_browser_read(request.headers)
    return _response(
        request.app.state.inventory.list_manual(
            _token(request, pagination=True),
            product_id=product_id,
            query=query,
            expected_actor=expected_membership(request),
        )
    )


@router.get("/products/{product_id}/history")
def product_history(
    product_id: UUID, request: Request, query: Annotated[HistoryPageQuery, Query()]
):
    request.app.state.http_security.verify_browser_read(request.headers)
    return _response(
        request.app.state.inventory.list_history(
            _token(request, pagination=True),
            product_id=product_id,
            query=query,
            expected_actor=expected_membership(request),
        )
    )
