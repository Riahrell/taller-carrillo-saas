"""Administrative grant changes; desired target UUID is never a tenant selector."""

import re
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.modules.access.administration_routes import _token
from app.modules.access.inventory_permissions import InventoryReadChange
from app.modules.access.invitation_routes import actor

router = APIRouter(prefix="/auth/memberships", tags=["inventory permissions"])


def response(view):
    return JSONResponse(
        view.model_dump(mode="json"),
        headers={
            "Cache-Control": "no-store",
            "ETag": view.etag,
            "X-Access-Membership": str(view.actor_membership_id),
        },
    )


@router.get("/{membership_id}/inventory-read")
def current(membership_id: UUID, request: Request):
    request.app.state.http_security.verify_browser_read(request.headers)
    return response(
        request.app.state.inventory_permissions.current(
            _token(request), membership_id=membership_id, expected_actor=actor(request)
        )
    )


@router.patch("/{membership_id}/inventory-read")
def change(membership_id: UUID, payload: InventoryReadChange, request: Request):
    values = request.headers.getlist("if-match")
    if not values:
        raise HTTPException(
            428, "Precondition required", headers={"Cache-Control": "no-store"}
        )
    if len(values) != 1 or not re.fullmatch(
        r'"inventory-read-v1-[0-9a-f-]{36}-(0|[1-9][0-9]{0,18})"', values[0]
    ):
        raise HTTPException(
            400, "Invalid precondition", headers={"Cache-Control": "no-store"}
        )
    return response(
        request.app.state.inventory_permissions.change(
            _token(request),
            membership_id=membership_id,
            expected_actor=actor(request),
            expected_etag=values[0],
            data=payload,
        )
    )


@router.get("/{membership_id}/inventory-withdrawal")
def current_withdrawal(membership_id: UUID, request: Request):
    request.app.state.http_security.verify_browser_read(request.headers)
    return response(
        request.app.state.inventory_withdrawal_permissions.current(
            _token(request), membership_id=membership_id, expected_actor=actor(request)
        )
    )


@router.patch("/{membership_id}/inventory-withdrawal")
def change_withdrawal(
    membership_id: UUID, payload: InventoryReadChange, request: Request
):
    values = request.headers.getlist("if-match")
    if not values:
        raise HTTPException(
            428, "Precondition required", headers={"Cache-Control": "no-store"}
        )
    if len(values) != 1 or not re.fullmatch(
        r'"inventory-withdrawal-v1-[0-9a-f-]{36}-(0|[1-9][0-9]{0,18})"', values[0]
    ):
        raise HTTPException(
            400, "Invalid precondition", headers={"Cache-Control": "no-store"}
        )
    return response(
        request.app.state.inventory_withdrawal_permissions.change(
            _token(request),
            membership_id=membership_id,
            expected_actor=actor(request),
            expected_etag=values[0],
            data=payload,
        )
    )
