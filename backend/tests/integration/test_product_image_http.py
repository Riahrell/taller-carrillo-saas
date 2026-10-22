from ipaddress import ip_address
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from identity_http_fixture import arrange_factor, login_with_legacy_factor
from test_product_image_service import images_state as images_state
from test_product_image_service import png, rows

from app.main import create_app
from app.modules.catalog.images import MAX_INPUT_BYTES

pytestmark = pytest.mark.integration
READ = {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def image_web(images_state, monkeypatch):
    state = images_state
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.2")},
    )
    with TestClient(
        create_app(state["settings"]),
        base_url=state["settings"].public_origin,
        client=("127.0.0.2", 12345),
        headers={"X-Real-IP": "127.0.0.1"},
    ) as client:
        client.app.state.product_images = state["service"]
        policy = client.app.state.http_security
        arrange_factor(state, client.app, monkeypatch)
        assert client.get("/auth/csrf", headers=READ).status_code == 204
        assert login_with_legacy_factor(client, state, policy).status_code == 200
        yield client, state, policy


def path(state, product=None):
    return f"/catalog/products/{product or state['product']}/image"


def upload(client, state, policy, *, key=None, body=None, headers=None):
    return client.post(
        path(state),
        content=png() if body is None else body,
        headers={
            "Origin": policy.origin,
            "Content-Type": "application/octet-stream",
            "X-CSRF-Token": client.cookies[policy.csrf_cookie],
            "X-Expected-Membership": str(state["membership"]),
            "Idempotency-Key": str(key or uuid4()),
            **(headers or {}),
        },
    )


def test_private_roundtrip_replay_headers_and_no_authority_fields(image_web):
    client, state, policy = image_web
    read = READ | {"X-Expected-Membership": str(state["membership"])}
    assert client.get(path(state), headers=read).status_code == 204
    key = uuid4()
    response = upload(client, state, policy, key=key)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "id",
        "product_id",
        "width",
        "height",
        "size",
        "created_at",
        "is_current",
    }
    assert upload(client, state, policy, key=key).json() == response.json()
    result = client.get(path(state), headers=read)
    assert result.status_code == 200
    assert result.content.startswith(b"RIFF")
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-access-membership"] == str(state["membership"])
    assert result.headers["content-type"] == "image/webp"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert len(rows(state)) == 1


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"Origin": "https://attacker.example"}, 403),
        ({"Content-Type": "image/png"}, 415),
        ({"Content-Length": str(MAX_INPUT_BYTES + 1)}, 413),
        ({"Idempotency-Key": "invalid"}, 422),
        ({"X-Expected-Membership": str(uuid4())}, 401),
    ],
)
def test_upload_guards_before_persistence(image_web, changes, status):
    client, state, policy = image_web
    assert upload(client, state, policy, headers=changes).status_code == status
    assert not rows(state)
    assert not state["storage"].objects


def test_invalid_binary_missing_context_and_foreign_reads(image_web):
    client, state, policy = image_web
    assert upload(client, state, policy, body=b"<svg/>").status_code == 422
    assert client.get(path(state), headers=READ).status_code == 428
    read = READ | {"X-Expected-Membership": str(state["membership"])}
    assert (
        client.get(path(state, state["foreign_product"]), headers=read).status_code
        == 401
    )
    assert not rows(state)


def test_unconfigured_storage_fails_closed_without_local_fallback(image_web):
    client, state, policy = image_web
    client.app.state.product_images = None
    assert upload(client, state, policy).status_code == 503
    assert not rows(state)
