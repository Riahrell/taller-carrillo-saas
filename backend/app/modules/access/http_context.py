"""Canonical expected context compares a session; it never selects authority."""

from uuid import UUID

from fastapi import Request

from app.modules.access.http_security import RequestRejected


def expected_membership(request: Request) -> UUID | None:
    values = request.headers.getlist("x-expected-membership")
    if not values:
        return None
    try:
        if len(values) != 1 or str(UUID(values[0])) != values[0]:
            raise ValueError
        return UUID(values[0])
    except ValueError:
        raise RequestRejected("Request rejected") from None
