"""Private purchase HTTP; CSRF and context never confer business authority."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, SecretStr

from app.modules.access.http_context import expected_membership
from app.modules.access.http_security import RequestRejected
from app.modules.access.membership import AccessDenied
from app.modules.purchases.inputs import (
    PurchaseCreation,
    PurchasePageQuery,
    ReceiptConfirmation,
    SupplierCreation,
)

router = APIRouter(prefix="/purchases", tags=["purchase administration"])
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


def _expected(request: Request) -> UUID:
    expected = expected_membership(request)
    if expected is None:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    return expected


def _key(request: Request) -> UUID:
    values = request.headers.getlist("idempotency-key")
    if not values:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    try:
        if len(values) != 1 or str(UUID(values[0])) != values[0]:
            raise ValueError
        return UUID(values[0])
    except ValueError:
        raise HTTPException(422, "Invalid request", headers=NO_STORE) from None


def _response(result: BaseModel, *, status: int = 200) -> JSONResponse:
    return JSONResponse(
        result.model_dump(mode="json"),
        status_code=status,
        headers=NO_STORE | {"X-Access-Membership": str(result.actor_membership_id)},
    )


@router.post("/suppliers", status_code=201)
def create_supplier(payload: SupplierCreation, request: Request):
    return _response(
        request.app.state.purchases.create_supplier(
            _token(request), data=payload, expected_actor=_expected(request)
        ),
        status=201,
    )


@router.get("/suppliers")
def list_suppliers(request: Request, query: Annotated[PurchasePageQuery, Query()]):
    request.app.state.http_security.verify_browser_read(request.headers)
    return _response(
        request.app.state.purchases.list_suppliers(
            _token(request, pagination=True),
            query=query,
            expected_actor=_expected(request),
        )
    )


@router.post("")
def record_purchase(payload: PurchaseCreation, request: Request):
    return _response(
        request.app.state.purchases.record_purchase(
            _token(request),
            data=payload,
            expected_actor=_expected(request),
            operation_key=_key(request),
        )
    )


@router.get("")
def list_purchases(request: Request, query: Annotated[PurchasePageQuery, Query()]):
    request.app.state.http_security.verify_browser_read(request.headers)
    return _response(
        request.app.state.purchases.list_purchases(
            _token(request, pagination=True),
            query=query,
            expected_actor=_expected(request),
        )
    )


@router.get("/{purchase_id}")
def current_purchase(purchase_id: UUID, request: Request):
    request.app.state.http_security.verify_browser_read(request.headers)
    return _response(
        request.app.state.purchases.current_purchase(
            _token(request), purchase_id=purchase_id, expected_actor=_expected(request)
        )
    )


@router.post("/{purchase_id}/receipt")
def receive_purchase(purchase_id: UUID, payload: ReceiptConfirmation, request: Request):
    return _response(
        request.app.state.purchase_receiver.receive(
            _token(request),
            purchase_id=purchase_id,
            expected_actor=_expected(request),
            operation_key=_key(request),
        )
    )


@router.get("/{purchase_id}/receipt")
def current_receipt(purchase_id: UUID, request: Request):
    request.app.state.http_security.verify_browser_read(request.headers)
    token, expected = _token(request), _expected(request)
    result = request.app.state.purchase_receiver.current_receipt(
        token, purchase_id=purchase_id, expected_actor=expected
    )
    if result is None:
        return Response(
            status_code=204, headers=NO_STORE | {"X-Access-Membership": str(expected)}
        )
    return _response(result)
