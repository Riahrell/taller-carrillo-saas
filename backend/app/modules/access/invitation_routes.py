"""Administrative invitation adapter; no public registration or mail dispatch."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.modules.access.administration import MembershipListQuery
from app.modules.access.administration_routes import _token
from app.modules.access.employee_invitations import InvitationConflict, InvitationInput
from app.modules.access.http_context import expected_membership
from app.modules.access.routes import EmptyInput

router = APIRouter(prefix="/auth/employee-invitations", tags=["employee invitations"])


def response(body, actor, status=200):
    return JSONResponse(
        body,
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Access-Membership": str(actor)},
    )


def actor(request):
    result = expected_membership(request)
    if result is None:
        raise HTTPException(
            428, "Precondition required", headers={"Cache-Control": "no-store"}
        )
    return result


@router.get("")
def list_invitations(request: Request, query: Annotated[MembershipListQuery, Query()]):
    request.app.state.http_security.verify_browser_read(request.headers)
    items, after, bound = request.app.state.employee_invitations.list(
        _token(request, pagination=True), query=query
    )
    return response(
        {
            "items": [item.model_dump(mode="json") for item in items],
            "next_after": str(after) if after else None,
        },
        bound,
    )


@router.post("", status_code=201)
def create_invitation(payload: InvitationInput, request: Request):
    try:
        item, secret, bound = request.app.state.employee_invitations.create(
            _token(request), data=payload, expected_actor=actor(request)
        )
    except InvitationConflict:
        raise HTTPException(
            409, "Invitation unavailable", headers={"Cache-Control": "no-store"}
        ) from None
    return response(
        {"invitation": item.model_dump(mode="json"), "code": secret.get_secret_value()},
        bound,
        201,
    )


@router.post("/{invitation_id}/revoke")
def revoke_invitation(invitation_id: UUID, payload: EmptyInput, request: Request):
    item, bound = request.app.state.employee_invitations.revoke(
        _token(request), invitation_id=invitation_id, expected_actor=actor(request)
    )
    return response(item.model_dump(mode="json"), bound)
