"""Command validation does not provide any identity or permission."""

import pytest
from pydantic import ValidationError

from app.modules.access.administration import MembershipChange, MembershipView
from app.modules.access.models import MembershipRole


@pytest.mark.parametrize("role", list(MembershipRole))
def test_explicit_supported_role(role):
    assert MembershipChange(role=role.value, is_active=True).role == role


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"role": "admin"},
        {"is_active": True},
        {"role": "root", "is_active": True},
        {"role": "admin", "is_active": 1},
        {"role": "admin", "is_active": "false"},
        {"role": "admin", "is_active": None},
        {"role": "admin", "is_active": True, "tenant_id": "chosen"},
        {"role": "admin", "is_active": True, "user_id": "chosen"},
        {"role": "admin", "is_active": True, "actor_role": "admin"},
    ],
)
def test_invalid_or_authority_fields_rejected(data):
    with pytest.raises(ValidationError):
        MembershipChange.model_validate(data)


@pytest.mark.parametrize("field", ["membership_id", "role", "is_active"])
def test_representation_etag_changes_with_each_public_attribute(field):
    from uuid import uuid4

    view = MembershipView(membership_id=uuid4(), role="cashier", is_active=True)
    values = {
        "membership_id": uuid4(),
        "role": MembershipRole.MECHANIC,
        "is_active": False,
    }
    changed = view.model_copy(update={field: values[field]})
    assert view.etag == MembershipView.model_validate_json(view.model_dump_json()).etag
    assert changed.etag != view.etag
    assert "etag" not in view.model_dump() and "token" not in view.model_dump()
