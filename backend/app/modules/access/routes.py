"""Same-origin cookie authentication. No tenant/role authority in request bodies."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, SecretStr

from app.modules.access.authentication import Credentials, PendingMfaLogin
from app.modules.access.http_context import expected_membership
from app.modules.access.http_security import RequestRejected
from app.modules.access.identity_http import browser_binding
from app.modules.access.login_challenges import PendingLogin
from app.modules.access.reauthentication import PasswordConfirmation

router = APIRouter(prefix="/auth", tags=["access"])


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class MembershipInput(EmptyInput):
    membership_id: UUID


def _denied() -> JSONResponse:
    return JSONResponse(
        {"detail": "Access denied"},
        status_code=401,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/csrf", status_code=204)
def csrf(request: Request) -> Response:
    policy, access = request.app.state.http_security, request.app.state.access
    policy.verify_browser_read(request.headers)
    cookies = policy.read_cookies(request.headers)
    token = cookies.get(policy.session_cookie)
    current = access.current(SecretStr(token)) if token else None
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    if current is not None:
        policy.refresh_csrf(response, current[1])
    elif policy.session_cookie in cookies:
        policy.clear_cookies(response)
        policy.start_preauth(response)
    else:
        policy.bootstrap_preauth(response, request.headers)
    return response


@router.get("/session")
def current_session(request: Request) -> Response:
    policy = request.app.state.http_security
    policy.verify_browser_read(request.headers)
    token = policy.read_cookies(request.headers).get(policy.session_cookie)
    current = request.app.state.access.current(SecretStr(token)) if token else None
    if current is None:
        response = _denied()
        if token is not None:
            policy.clear_cookies(response)
        return response
    return JSONResponse(
        current[0].model_dump(mode="json"), headers={"Cache-Control": "no-store"}
    )


@router.post("/login")
def login(payload: Credentials, request: Request) -> Response:
    policy, access = request.app.state.http_security, request.app.state.access
    policy.login_binding(request.headers)
    binding = browser_binding(policy, request.headers)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    request.app.state.identity.cancel(binding)
    with access.password_slot():
        result = access.authenticator.begin_mfa_login(
            payload, client_ip=peer, browser_binding=binding
        )
    if result is None:
        return _denied()
    if isinstance(result, PendingMfaLogin):
        response = JSONResponse(
            {
                "status": "mfa_totp"
                if result.purpose.value == "login_totp"
                else "mfa_enrollment"
            },
            status_code=202,
            headers={"Cache-Control": "no-store"},
        )
        policy.set_challenge(response, result)
        return response
    if isinstance(result, PendingLogin):
        response = JSONResponse(
            {
                "status": "select_membership",
                "choices": [
                    {
                        "membership_id": str(choice.membership_id),
                        "tenant_name": choice.tenant_name,
                    }
                    for choice in result.choices
                ],
            },
            status_code=202,
            headers={"Cache-Control": "no-store"},
        )
        policy.set_challenge(response, result)
    else:
        response = JSONResponse(
            {"status": "authenticated"}, headers={"Cache-Control": "no-store"}
        )
        policy.set_session(response, result)
    return response


@router.post("/select-membership")
def select_membership(payload: MembershipInput, request: Request) -> Response:
    policy, access = request.app.state.http_security, request.app.state.access
    policy.login_binding(request.headers)
    binding = browser_binding(policy, request.headers)
    token = policy.read_cookies(request.headers).get(policy.challenge_cookie)
    result = request.app.state.identity.sessions.select_membership(
        proof_token=SecretStr(token or ""),
        browser_binding=binding,
        membership_id=payload.membership_id,
    )
    if result is None:
        result = access.authenticator.select_membership(
            challenge_token=SecretStr(token or ""),
            browser_binding=binding,
            membership_id=payload.membership_id,
        )
    if result is None:
        response = _denied()
        policy.clear_challenge(response)
        return response
    response = JSONResponse(
        {"status": "authenticated"}, headers={"Cache-Control": "no-store"}
    )
    policy.set_session(response, result)
    return response


@router.post("/reauthenticate", status_code=204)
def reauthenticate(payload: PasswordConfirmation, request: Request) -> Response:
    # Middleware bounds JSON and verifies same-origin session-bound CSRF first.
    if request.query_params:
        raise HTTPException(
            422, "Invalid request", headers={"Cache-Control": "no-store"}
        )
    policy, access = request.app.state.http_security, request.app.state.access
    cookies = policy.read_cookies(request.headers)
    token = cookies.get(policy.session_cookie)
    if token is None:
        return _denied()
    if policy.preauth_cookie in cookies or policy.challenge_cookie in cookies:
        raise RequestRejected("Request rejected")
    expected = expected_membership(request)
    if expected is None:
        raise HTTPException(
            428, "Precondition required", headers={"Cache-Control": "no-store"}
        )
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    with access.password_slot():
        issued = access.authenticator.reauthenticate(
            SecretStr(token), data=payload, expected_actor=expected, client_ip=peer
        )
    if issued is None:
        return _denied()
    response = Response(
        status_code=204,
        headers={"Cache-Control": "no-store", "X-Access-Membership": str(expected)},
    )
    # Cookie/token/CSRF are published only after the service commits rotation.
    policy.set_session(response, issued)
    return response


@router.post("/logout", status_code=204)
def logout(_payload: EmptyInput, request: Request) -> Response:
    policy = request.app.state.http_security
    cookies = policy.read_cookies(request.headers)
    try:
        binding = browser_binding(policy, request.headers)
    except RequestRejected:
        binding = None
    session_token, challenge = (
        cookies.get(policy.session_cookie),
        cookies.get(policy.challenge_cookie),
    )
    request.app.state.access.logout(
        session_token=SecretStr(session_token) if session_token else None,
        challenge_token=SecretStr(challenge) if challenge else None,
        browser_binding=binding,
    )
    response = Response(status_code=204)
    policy.clear_cookies(response)
    return response
