from decimal import Decimal, Inexact, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.inventory.inputs import PhysicalCount
from app.modules.inventory.valuation import StockValue

D = Decimal


def test_count_preserves_balance_on_no_difference_and_values_shortage():
    balance = StockValue(D(3), D(1))
    assert balance.recount(D(3)) is balance
    assert balance.recount(D(2)) == balance.withdraw(D(1))
    assert balance.recount(D(0)) == StockValue(D(0), D(0))


def test_surplus_uses_historical_ratio_without_rounding_unit_cost_first():
    assert StockValue(D(3), D(1)).recount(D(6)) == StockValue(D(6), D(2))


@pytest.mark.parametrize("balance", [StockValue(D(0), D(0)), StockValue(D(2), D(0))])
def test_surplus_without_usable_cost_requires_explicit_declaration(balance):
    counted = balance.quantity + 1
    with pytest.raises(ValueError):
        balance.recount(counted)
    assert balance.recount(counted, declared_unit_cost=D(0)).value == 0
    assert balance.recount(counted, declared_unit_cost=D("1.234567")).value == D(
        "1.234567"
    )


@pytest.mark.parametrize("counted", [D(0), D(1), D(2), D(3)])
def test_count_cannot_override_historical_cost(counted):
    with pytest.raises(ValueError):
        StockValue(D(2), D(4)).recount(counted, declared_unit_cost=D(100))


def test_recount_is_independent_of_callers_decimal_context():
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        result = StockValue(D("123.456"), D("987.654321")).recount(D("246.912"))
    assert result == StockValue(D("246.912"), D("1975.308642"))


def request(**changes):
    return PhysicalCount.model_validate(
        dict(product_id=uuid4(), counted_quantity="1.234", reason=" Conteo físico ")
        | changes
    )


def test_count_request_trims_reason_and_serializes_exact_strings():
    value = request(declared_unit_cost="12.123456")
    assert value.reason == "Conteo físico"
    assert value.model_dump(mode="json")["counted_quantity"] == "1.234"
    assert value.model_dump(mode="json")["declared_unit_cost"] == "12.123456"


@pytest.mark.parametrize(
    "field",
    [
        "tenant_id",
        "actor_id",
        "role",
        "previous_stock",
        "quantity_delta",
        "value",
        "price",
    ],
)
def test_client_cannot_supply_authoritative_fields(field):
    with pytest.raises(ValidationError):
        request(**{field: "untrusted"})


@pytest.mark.parametrize(
    "cost", [True, 1.2, "NaN", "Infinity", "-1", "0.0000001", "1e100000", "x", "1" * 65]
)
def test_invalid_declared_cost(cost):
    with pytest.raises(ValidationError):
        request(declared_unit_cost=cost)


@pytest.mark.parametrize("reason", ["", "  ", "x\x00", "\ud800", "x" * 501])
def test_invalid_count_reason(reason):
    with pytest.raises(ValidationError):
        request(reason=reason)
