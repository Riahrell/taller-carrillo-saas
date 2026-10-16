"""Pagination input is bounded and never provides tenant or permission."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.access.administration import MembershipListQuery


@pytest.mark.parametrize("limit", [1, 25, 100, "1", "25", "100"])
def test_bounded_canonical_limit(limit):
    assert MembershipListQuery(limit=limit).limit == int(limit)


def test_default_and_uuid_cursor():
    cursor = uuid4()
    assert MembershipListQuery().limit == 25
    assert MembershipListQuery(after=str(cursor)).after == cursor


@pytest.mark.parametrize(
    "limit", [0, -1, 101, True, 1.0, None, "01", "+1", " 1", "1.0", "١", "9" * 1000]
)
def test_invalid_limit_rejected(limit):
    with pytest.raises(ValidationError):
        MembershipListQuery(limit=limit)


@pytest.mark.parametrize(
    "data",
    [
        {"after": "invalid"},
        {"tenant_id": str(uuid4())},
        {"role": "admin"},
        {"offset": 0},
        {"sort": "email"},
    ],
)
def test_extra_authority_or_invalid_cursor_rejected(data):
    with pytest.raises(ValidationError):
        MembershipListQuery.model_validate(data)
