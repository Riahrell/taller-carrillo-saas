"""Same-origin identity routes. Every mutation requires the existing CSRF guard.

No identity/role/tenant/price supplied by the client becomes authority. Link
secrets arrive by POST body only; GET never consumes. Pending cookies HttpOnly.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select

from app.modules.access.http_context import expected_membership
from app.modules.access.http_security import RequestRejected
from app.modules.access.identity_http import browser_binding
from app.modules.access.login_challenges import PendingLogin
from app.modules.access.password_reset import (
    PasswordResetConfirmation,
    PasswordResetRequest,
)
from app.modules.access.reauthentication import MfaPasswordConfirmation


def no_query(request: Request):
    if request.query_params:
        raise HTTPException(
            422, "Invalid request", headers={"Cache-Control": "no-store"}
        )


router = APIRouter(prefix="/auth", dependencies=[Depends(no_query)])


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Code(Empty):
    code: SecretStr = Field(min_length=1, max_length=128, repr=False, exclude=True)


class LinkToken(Empty):
    token: SecretStr = Field(min_length=43, max_length=43, repr=False, exclude=True)


def state(request):
    return request.app.state.identity.require(), request.app.state.http_security


def pending(request, policy):
    policy.login_binding(request.headers)
    token = policy.read_cookies(request.headers).get(policy.challenge_cookie, "")
    return SecretStr(token), browser_binding(policy, request.headers)


def session_token(request, policy):
    token = policy.read_cookies(request.headers).get(policy.session_cookie)
    if token is None:
        raise HTTPException(401, "Access denied")
    return SecretStr(token)


def denied():
    return JSONResponse(
        {"detail": "Access denied"},
        status_code=401,
        headers={"Cache-Control": "no-store"},
    )


def complete(result, policy):
    if result is None:
        return denied()
    if isinstance(result, PendingLogin):
        response = JSONResponse(
            {
                "status": "select_membership",
                "choices": [
                    {
                        "membership_id": str(item.membership_id),
                        "tenant_name": item.tenant_name,
                    }
                    for item in result.choices
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


def setup_response(result, policy):
    if result is None:
        return denied()
    import base64

    import pyotp
    import qrcode
    from qrcode.image.svg import SvgPathImage

    seed = result.seed.base32().get_secret_value()
    uri = pyotp.TOTP(seed).provisioning_uri(
        name="Cuenta", issuer_name="Taller Carrillo"
    )
    qr = "data:image/svg+xml;base64," + base64.b64encode(
        qrcode.make(uri, image_factory=SvgPathImage).to_string()
    ).decode("ascii")
    response = JSONResponse(
        {"status": "confirm_factor", "seed": seed, "qr": qr},
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
    policy.set_challenge(response, result)
    return response


def bundle_response(result, policy):
    if result is None:
        return denied()
    response = JSONResponse(
        {"codes": [item.value.get_secret_value() for item in result.codes]},
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
    policy.clear_cookies(response)
    policy.start_preauth(response)
    return response


@router.post("/mfa/totp")
def totp(data: Code, request: Request):
    identity, policy = state(request)
    token, binding = pending(request, policy)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    proof = identity.verifier.verify_login_totp(
        token=token, browser_binding=binding, code=data.code, client_ip=peer
    )
    return complete(
        identity.sessions.finish(proof_token=proof.token, browser_binding=binding)
        if proof
        else None,
        policy,
    )


@router.post("/mfa/enrollment/email", status_code=204)
def enrollment_email(_data: Empty, request: Request):
    identity, policy = state(request)
    token, binding = pending(request, policy)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    draft = identity.enrollment_mail.issue(
        enrollment_token=token, browser_binding=binding, client_ip=peer
    )
    if draft is None:
        return denied()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/mfa/enrollment/confirm")
def enrollment_confirm(data: LinkToken, request: Request):
    identity, policy = state(request)
    token, binding = pending(request, policy)
    permit = identity.enrollment_mail.confirm(
        enrollment_token=token, browser_binding=binding, email_token=data.token
    )
    setup = (
        identity.enrollment.begin(permit_token=permit.token, browser_binding=binding)
        if permit
        else None
    )
    return setup_response(setup, policy)


@router.post("/mfa/enrollment/activate")
def enrollment_activate(data: Code, request: Request):
    identity, policy = state(request)
    token, binding = pending(request, policy)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    return bundle_response(
        identity.enrollment.activate(
            setup_token=token, browser_binding=binding, code=data.code, client_ip=peer
        ),
        policy,
    )


@router.post("/mfa/recovery")
def recovery(data: Code, request: Request):
    identity, policy = state(request)
    token, binding = pending(request, policy)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    permit = identity.recovery.begin(
        password_token=token, browser_binding=binding, code=data.code, client_ip=peer
    )
    setup = (
        identity.replacement.begin(permit_token=permit.token, browser_binding=binding)
        if permit
        else None
    )
    return setup_response(setup, policy)


@router.post("/mfa/replacement/activate")
def replacement_activate(data: Code, request: Request):
    identity, policy = state(request)
    token, binding = pending(request, policy)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    return bundle_response(
        identity.replacement.activate(
            setup_token=token, browser_binding=binding, code=data.code, client_ip=peer
        ),
        policy,
    )


@router.post("/mfa/codes")
def regenerate(_data: Empty, request: Request):
    identity, policy = state(request)
    actor = expected_membership(request)
    if actor is None:
        raise HTTPException(428, "Precondition required")
    return bundle_response(
        identity.regenerate.regenerate(
            session_token=session_token(request, policy), expected_actor=actor
        ),
        policy,
    )


@router.post("/mfa/reauthenticate", status_code=204)
def reauthenticate(data: MfaPasswordConfirmation, request: Request):
    identity, policy = state(request)
    token = session_token(request, policy)
    cookies = policy.read_cookies(request.headers)
    if policy.preauth_cookie in cookies or policy.challenge_cookie in cookies:
        raise RequestRejected("Request rejected")
    actor = expected_membership(request)
    if actor is None:
        raise HTTPException(428, "Precondition required")
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    access = request.app.state.access
    with access.password_slot():
        result = access.authenticator.reauthenticate_mfa(
            token,
            data=data,
            expected_actor=actor,
            client_ip=peer,
            keyring=identity.keyring,
        )
    if result is None:
        return denied()
    response = Response(
        status_code=204,
        headers={"X-Access-Membership": str(actor), "Cache-Control": "no-store"},
    )
    policy.set_session(response, result)
    return response


@router.post("/password-reset/request", status_code=204)
def reset_request(data: PasswordResetRequest, request: Request):
    identity, _ = state(request)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    identity.reset_inbox.submit(data, client_ip=peer)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/password-reset/confirm", status_code=204)
def reset_confirm(data: PasswordResetConfirmation, request: Request):
    identity, policy = state(request)
    with request.app.state.access.password_slot():
        result = identity.reset_consumer.consume(data)
    if not result:
        return denied()
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    policy.clear_cookies(response)
    policy.start_preauth(response)
    return response


@router.post("/mailbox/request", status_code=204)
def mailbox_request(_data: Empty, request: Request):
    identity, policy = state(request)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    actor = expected_membership(request)
    if actor is None:
        raise HTTPException(428, "Precondition required")
    identity.mailbox.issue(
        session_token(request, policy), client_ip=peer, expected_actor=actor
    )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/mailbox/confirm", status_code=204)
def mailbox_confirm(data: LinkToken, request: Request):
    identity, policy = state(request)
    actor = expected_membership(request)
    if actor is None:
        raise HTTPException(428, "Precondition required")
    if not identity.mailbox.confirm(
        session_token(request, policy), token=data.token, expected_actor=actor
    ):
        return denied()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/security")
def security(request: Request):
    from app.modules.access.mfa_verification import _timeouts
    from app.modules.access.models import (
        MfaCredential,
        MfaPending,
        SessionRecord,
        VerifiedMailbox,
    )
    from app.modules.access.sessions import _database_now, _token_hash, resolve_session

    identity, policy = state(request)
    policy.verify_browser_read(request.headers)
    with identity._factory.begin() as session:
        _timeouts(session)
        actor = resolve_session(session, session_token(request, policy))
        record = session.scalar(
            select(SessionRecord).where(
                SessionRecord.token_hash == _token_hash(session_token(request, policy))
            )
        )
        factor = session.scalar(
            select(MfaCredential.id).where(
                MfaCredential.user_id == actor.user_id,
                MfaCredential.revoked_at.is_(None),
            )
        )
        verified = session.scalar(
            select(VerifiedMailbox.id).where(
                VerifiedMailbox.user_id == actor.user_id,
                VerifiedMailbox.invalidated_at.is_(None),
            )
        )
        recent = 0
        if record.mfa_pending_id:
            proof = session.get(MfaPending, record.mfa_pending_id)
            oldest = min(proof.password_verified_at, proof.factor_verified_at)
            if record.mfa_reauthenticated_at is not None:
                oldest = min(record.reauthenticated_at, record.mfa_reauthenticated_at)
            recent = max(
                0,
                min(300, int((oldest - _database_now(session)).total_seconds()) + 300),
            )
        from app.modules.access.models import Tenant

        confirmation_seconds = (
            max(0, int((record.expires_at - _database_now(session)).total_seconds()))
            if record.email_login_id is not None
            else 0
        )
        confirmation_context = (
            {
                "user_id": str(actor.user_id),
                "tenant_id": str(actor.tenant_id),
                "membership_id": str(actor.membership_id),
                "role": actor.role.value,
                "tenant_name": session.get(Tenant, actor.tenant_id).name,
            }
            if confirmation_seconds > 0
            else None
        )
        result = {
            "confirmation_context": confirmation_context,
            "confirmation_seconds": confirmation_seconds,
            "mfa_enabled": factor is not None and record.email_login_id is None,
            "mailbox_verified": verified is not None,
            "recent_seconds": recent,
        }
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/sessions/revoke-all", status_code=204)
def revoke_all(_data: Empty, request: Request):
    from app.modules.access.mfa_verification import _timeouts
    from app.modules.access.models import User
    from app.modules.access.security_state import advance_security_revision
    from app.modules.access.sessions import resolve_session

    identity, policy = state(request)
    expected = expected_membership(request)
    if expected is None:
        raise HTTPException(428, "Precondition required")
    with identity._factory.begin() as session:
        _timeouts(session)
        actor = resolve_session(session, session_token(request, policy))
        if actor.membership_id != expected:
            return denied()
        user = session.get(User, actor.user_id)
        advance_security_revision(
            session, user_id=user.id, expected_revision=user.security_revision
        )
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    policy.clear_cookies(response)
    policy.start_preauth(response)
    return response


@router.get("/security/events")
def security_events(request: Request):
    from app.modules.access.mfa_verification import _timeouts
    from app.modules.access.models import SecurityEvent
    from app.modules.access.sessions import resolve_session

    identity, policy = state(request)
    policy.verify_browser_read(request.headers)
    with identity._factory.begin() as session:
        _timeouts(session)
        actor = resolve_session(session, session_token(request, policy))
        rows = session.scalars(
            select(SecurityEvent)
            .where(SecurityEvent.user_id == actor.user_id)
            .order_by(SecurityEvent.occurred_at.desc(), SecurityEvent.id.desc())
            .limit(50)
        ).all()
        data = [
            {"action": row.action, "occurred_at": row.occurred_at.isoformat()}
            for row in rows
        ]
    return JSONResponse({"items": data}, headers={"Cache-Control": "no-store"})


@router.get("/sessions")
def list_sessions(request: Request):
    from app.modules.access.mfa_verification import _timeouts
    from app.modules.access.models import SessionRecord, TenantMembership
    from app.modules.access.sessions import (
        IDLE_TIMEOUT,
        _database_now,
        _token_hash,
        resolve_session,
    )

    identity, policy = state(request)
    policy.verify_browser_read(request.headers)
    token = session_token(request, policy)
    with identity._factory.begin() as session:
        _timeouts(session)
        actor = resolve_session(session, token)
        now = _database_now(session)
        rows = session.scalars(
            select(SessionRecord)
            .join(TenantMembership, SessionRecord.membership_id == TenantMembership.id)
            .where(
                TenantMembership.user_id == actor.user_id,
                SessionRecord.revoked_at.is_(None),
                SessionRecord.expires_at > now,
                SessionRecord.last_seen_at > now - IDLE_TIMEOUT,
            )
            .order_by(SessionRecord.created_at.desc())
            .limit(100)
        ).all()
        data = [
            {
                "session_id": str(row.id),
                "created_at": row.created_at.isoformat(),
                "last_seen_at": row.last_seen_at.isoformat(),
                "expires_at": row.expires_at.isoformat(),
                "current": row.token_hash == _token_hash(token),
            }
            for row in rows
        ]
    return JSONResponse({"items": data}, headers={"Cache-Control": "no-store"})


@router.post("/mfa/rotation/begin")
def rotation_begin(_data: Empty, request: Request):
    identity, policy = state(request)
    actor = expected_membership(request)
    if actor is None:
        raise HTTPException(428, "Precondition required")
    return setup_response(
        identity.rotation.begin(session_token(request, policy), expected_actor=actor),
        policy,
    )


@router.post("/mfa/rotation/activate")
def rotation_activate(data: Code, request: Request):
    identity, policy = state(request)
    actor = expected_membership(request)
    if actor is None:
        raise HTTPException(428, "Precondition required")
    setup = SecretStr(
        policy.read_cookies(request.headers).get(policy.challenge_cookie, "")
    )
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    return bundle_response(
        identity.rotation.activate(
            session_token(request, policy),
            setup_token=setup,
            code=data.code,
            expected_actor=actor,
            client_ip=peer,
        ),
        policy,
    )


class EmailCode(Empty):
    code: SecretStr = Field(min_length=6, max_length=6, repr=False, exclude=True)


@router.post("/email-login/request", status_code=204)
def email_login_request(_data: Empty, request: Request):
    identity, policy = state(request)
    token, binding = pending(request, policy)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    if not identity.email_login.issue(token, binding, peer):
        return JSONResponse(
            {"detail": "Request unavailable"},
            status_code=429,
            headers={"Cache-Control": "no-store"},
        )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/email-login/confirm")
def email_login_confirm(data: EmailCode, request: Request):
    identity, policy = state(request)
    token, binding = pending(request, policy)
    peer = request.app.state.trusted_proxy.client_ip(request.scope, request.headers)
    confirmed = identity.email_login.confirm(token, binding, data.code, peer)
    if confirmed is None:
        return denied()
    result, remembered, expires = confirmed
    response = complete(result, policy)
    response.set_cookie(
        policy.browser_cookie,
        remembered.get_secret_value(),
        max_age=15 * 24 * 60 * 60,
        expires=expires,
        path="/",
        secure=policy.secure,
        httponly=True,
        samesite="strict",
    )
    return response
