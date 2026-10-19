from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.inventory.valuation import StockValue
from app.modules.purchases.calculation import MAX_AMOUNT, calculate_purchase
from app.modules.purchases.inputs import MAX_PURCHASE_LINES, PurchaseCreation

D = Decimal


def purchase(*lines):
    return PurchaseCreation(supplier_id=uuid4(), lines=lines)


def line(quantity="1", cost="2", **extra):
    return dict(product_id=uuid4(), quantity=quantity, unit_cost=cost, **extra)


def test_fractional_receipt_value_and_monetary_total_are_separate():
    result = calculate_purchase(purchase(line("0.125", "3.123456"), line("2", "4")))
    assert result.lines[0].stock_value == D("0.390432")
    assert result.lines[0].amount == D("0.39")
    assert result.total == D("8.39")
    balance = StockValue(D(2), D(4))
    received = balance.receive(result.lines[0].quantity, result.lines[0].unit_cost)
    assert received == StockValue(D("2.125"), D("4.390432"))
    assert balance == StockValue(D(2), D(4))


def test_money_rounds_each_exact_line_half_up_without_double_rounding():
    result = calculate_purchase(purchase(line("0.001", "4.999999"), line("0.001", "5")))
    assert result.lines[0].stock_value == D("0.005000")
    assert result.lines[0].amount == D("0.00")
    assert result.lines[1].amount == D("0.01")
    assert result.total == D("0.01")
    assert calculate_purchase(
        purchase(line("1", "0.005"), line("1", "0.005"))
    ).total == D("0.02")


def test_ambient_context_cannot_change_amounts_or_raise_inexact():
    command = purchase(line("123.125", "123456.123456"))
    expected = calculate_purchase(command)
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        assert calculate_purchase(command) == expected


@pytest.mark.parametrize("cost", ["0", 0, D("0.000000")])
def test_explicit_zero_cost_is_allowed(cost):
    result = calculate_purchase(purchase(line(cost=cost)))
    assert result.total == result.lines[0].stock_value == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "0"),
        ("quantity", "-1"),
        ("quantity", "1.0001"),
        ("quantity", 1.5),
        ("quantity", True),
        ("quantity", "Infinity"),
        ("quantity", "1000000000000000"),
        ("unit_cost", -1),
        ("unit_cost", 0.1),
        ("unit_cost", False),
        ("unit_cost", "NaN"),
        ("unit_cost", "Infinity"),
        ("unit_cost", "0.0000001"),
        ("unit_cost", "10000000000000000000000"),
        ("unit_cost", "1" * 65),
        ("unit_cost", None),
    ],
)
def test_inexact_or_out_of_range_inputs_are_rejected(field, value):
    data = line()
    data[field] = value
    with pytest.raises(ValidationError):
        purchase(data)


@pytest.mark.parametrize("field", ["tenant_id", "role", "stock", "price", "total"])
def test_client_cannot_supply_authority_or_derived_values(field):
    with pytest.raises(ValidationError):
        PurchaseCreation(supplier_id=uuid4(), lines=[line()], **{field: "forged"})
    with pytest.raises(ValidationError):
        purchase(line(**{field: "forged"}))


def test_cost_is_required_and_duplicate_products_are_rejected():
    with pytest.raises(ValidationError):
        purchase(dict(product_id=uuid4(), quantity="1"))
    item = line()
    with pytest.raises(ValidationError):
        purchase(item, item)


def test_line_limit_and_immutable_collection():
    with pytest.raises(ValidationError):
        purchase()
    with pytest.raises(ValidationError):
        purchase(*(line() for _ in range(MAX_PURCHASE_LINES + 1)))
    command = purchase(*(line() for _ in range(MAX_PURCHASE_LINES)))
    assert calculate_purchase(command).total == D(200)
    assert isinstance(command.lines, tuple)
    with pytest.raises(ValidationError):
        command.lines[0].quantity = D(2)


def test_internal_unvalidated_copy_is_rejected():
    command = purchase(line())
    invalid = command.model_copy(
        update={"lines": (command.lines[0].model_copy(update={"quantity": D(-1)}),)}
    )
    with pytest.raises(ValidationError):
        calculate_purchase(invalid)


def test_line_and_total_capacity_are_checked_without_rounding_overflow():
    assert calculate_purchase(purchase(line(cost=str(MAX_AMOUNT)))).total == MAX_AMOUNT
    with pytest.raises(ValueError):
        calculate_purchase(purchase(line(cost=str(MAX_AMOUNT)), line(cost="0.01")))
    with pytest.raises(ValueError):
        calculate_purchase(purchase(line("2", str(MAX_AMOUNT))))
    with pytest.raises(ValueError):
        calculate_purchase(
            purchase(line("999999999999999.999", "9999999999999999999999.999999"))
        )


def test_suppliers_and_products_are_references_not_authorization():
    command = purchase(line())
    # This pure layer accepts identifiers but cannot establish tenant ownership.
    # No session, database or persistence effect is introduced here.
    assert (
        calculate_purchase(command).lines[0].product_id == command.lines[0].product_id
    )
