"""Command validation does not provide any identity or permission."""

import pytest
from pydantic import ValidationError

from app.modules.access.administration import MembershipChange
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
