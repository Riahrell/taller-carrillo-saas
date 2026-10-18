"""Private administrative catalog; CSRF is browser intent, never authority."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr

from app.modules.access.http_context import expected_membership
from app.modules.access.http_security import RequestRejected
from app.modules.access.membership import AccessDenied
from app.modules.catalog.inputs import (
    CatalogPageQuery,
    CategoryCreation,
    ProductCreation,
)

router = APIRouter(prefix="/catalog", tags=["catalog administration"])
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


def _expected_write(request: Request) -> UUID:
    expected = expected_membership(request)
    if expected is None:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    return expected


def _response(result: BaseModel, *, status: int = 200) -> JSONResponse:
    return JSONResponse(
        result.model_dump(mode="json"),
        status_code=status,
        headers=NO_STORE | {"X-Access-Membership": str(result.actor_membership_id)},
    )


@router.post("/categories", status_code=201)
def create_category(payload: CategoryCreation, request: Request) -> JSONResponse:
    result = request.app.state.catalog.create_category(
        _token(request), data=payload, expected_actor=_expected_write(request)
    )
    return _response(result, status=201)


@router.post("/products", status_code=201)
def create_product(payload: ProductCreation, request: Request) -> JSONResponse:
    result = request.app.state.catalog.create_product(
        _token(request), data=payload, expected_actor=_expected_write(request)
    )
    return _response(result, status=201)


@router.get("/categories")
def list_categories(request: Request, query: Annotated[CatalogPageQuery, Query()]):
    request.app.state.http_security.verify_browser_read(request.headers)
    return _response(
        request.app.state.catalog.list_categories(
            _token(request, pagination=True),
            query=query,
            expected_actor=expected_membership(request),
        )
    )


@router.get("/products")
def list_products(request: Request, query: Annotated[CatalogPageQuery, Query()]):
    request.app.state.http_security.verify_browser_read(request.headers)
    return _response(
        request.app.state.catalog.list_products(
            _token(request, pagination=True),
            query=query,
            expected_actor=expected_membership(request),
        )
    )


@router.get("/products/{product_id}")
def current_product(product_id: UUID, request: Request):
    request.app.state.http_security.verify_browser_read(request.headers)
    return _response(
        request.app.state.catalog.current_product(
            _token(request),
            product_id=product_id,
            expected_actor=expected_membership(request),
        )
    )
