"""Private stock HTTP; tenant, prior stock and deltas are never browser authority."""

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr

from app.modules.access.http_context import expected_membership
from app.modules.access.http_security import RequestRejected
from app.modules.access.membership import AccessDenied
from app.modules.catalog.inputs import CatalogPageQuery, ProductSearchQuery
from app.modules.inventory.inputs import (
    CountPageQuery,
    HistoryPageQuery,
    ManualStockInput,
    PhysicalCount,
    StockMinimumChange,
    StockReversalInput,
)
from app.modules.inventory.withdrawals import WithdrawalInput

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


@router.get("/products/{product_id}/minimum")
def get_minimum(product_id: UUID, request: Request):
    request.app.state.http_security.verify_browser_read(request.headers)
    result = request.app.state.inventory.minimum(
        _token(request),
        product_id=product_id,
        expected_actor=expected_membership(request),
    )
    response = _response(result)
    response.headers["ETag"] = result.etag
    return response


@router.patch("/products/{product_id}/minimum")
def change_minimum(product_id: UUID, payload: StockMinimumChange, request: Request):
    values = request.headers.getlist("if-match")
    if not values:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    if (
        len(values) != 1
        or re.fullmatch(
            r'"stock-minimum-v1-[0-9a-f-]{36}-(0|[1-9][0-9]{0,18})"', values[0]
        )
        is None
    ):
        raise HTTPException(400, "Invalid precondition", headers=NO_STORE)
    expected = expected_membership(request)
    if expected is None:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    result = request.app.state.inventory.minimum(
        _token(request),
        product_id=product_id,
        expected_actor=expected,
        data=payload,
        expected_etag=values[0],
    )
    response = _response(result)
    response.headers["ETag"] = result.etag
    return response


@router.get("/low-stock")
def list_low_stock(request: Request, query: Annotated[CountPageQuery, Query()]):
    request.app.state.http_security.verify_browser_read(request.headers)
    return _response(
        request.app.state.inventory.low_stock(
            _token(request, pagination=True),
            query=query,
            expected_actor=expected_membership(request),
        )
    )


@router.post("/reversals")
def reverse_stock(payload: StockReversalInput, request: Request):
    token = _token(request)
    expected = expected_membership(request)
    if expected is None:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    return _response(
        request.app.state.inventory.reverse(
            token,
            data=payload,
            operation_key=_operation_key(request),
            expected_actor=expected,
        )
    )


@router.get("/availability")
def available_products(request: Request, query: Annotated[ProductSearchQuery, Query()]):
    request.app.state.http_security.verify_browser_read(request.headers)
    expected = expected_membership(request)
    if expected is None:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    return _response(
        request.app.state.inventory_availability.products(
            _token(request, pagination=True), expected_actor=expected, query=query
        )
    )


@router.get("/availability/categories")
def available_categories(request: Request, query: Annotated[CatalogPageQuery, Query()]):
    request.app.state.http_security.verify_browser_read(request.headers)
    expected = expected_membership(request)
    if expected is None:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    return _response(
        request.app.state.inventory_availability.categories(
            _token(request, pagination=True), expected_actor=expected, query=query
        )
    )


@router.get("/availability/products/{product_id}/image/thumbnail")
def available_thumbnail(product_id: UUID, request: Request):
    return _available_image(product_id, request, thumbnail=True)


@router.get("/availability/products/{product_id}/image")
def available_image(product_id: UUID, request: Request):
    return _available_image(product_id, request)


def _available_image(product_id: UUID, request: Request, *, thumbnail=False):
    from fastapi.responses import Response

    request.app.state.http_security.verify_browser_read(request.headers)
    token, expected = _token(request), expected_membership(request)
    if expected is None:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    service = request.app.state.product_images
    if service is None:
        raise HTTPException(503, "Images unavailable", headers=NO_STORE)
    result = service.read(
        token,
        expected_actor=expected,
        product_id=product_id,
        operational=True,
        thumbnail=thumbnail,
    )
    headers = NO_STORE | {
        "X-Access-Membership": str(expected),
        "X-Content-Type-Options": "nosniff",
    }
    if result is None:
        return Response(status_code=204, headers=headers)
    image, content = result
    return Response(
        content,
        media_type=image.content_type,
        headers=headers
        | {
            "X-Image-Id": str(image.id),
            "Content-Disposition": 'inline; filename="product.webp"'
            if image.content_type == "image/webp"
            else 'inline; filename="product.png"',
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.post("/availability/withdrawals")
def operational_withdrawal(payload: WithdrawalInput, request: Request):
    expected = expected_membership(request)
    if expected is None:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    return _response(
        request.app.state.inventory_withdrawals.record(
            _token(request),
            data=payload,
            operation_key=_operation_key(request),
            expected_actor=expected,
        )
    )
