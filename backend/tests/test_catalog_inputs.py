"""Catalog contracts reject client authority and preserve exact prices."""

from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from app.modules.catalog.inputs import (
    CatalogPageQuery,
    CategoryCreation,
    ProductCreation,
)


@pytest.mark.parametrize(
    "price", [0, "0.00", "12.34", Decimal("0.01"), "999999999999999999.99"]
)
def test_price_is_exact_and_serialized_as_string(price):
    data = ProductCreation(name="  Filtro  ", sku="  Ab-1  ", price=price)
    assert data.name == "Filtro"
    assert data.sku == "Ab-1"
    assert data.price == Decimal(price)
    assert isinstance(data.price, Decimal)
    assert ProductCreation.model_validate_json(data.model_dump_json()) == data


@pytest.mark.parametrize(
    "price",
    [
        True,
        False,
        0.1,
        1.0,
        None,
        "NaN",
        "Infinity",
        "-Infinity",
        "-0.01",
        "1.001",
        "1.000",
        "1000000000000000000",
        "1e99999",
        "1e-99999",
        "",
        "x",
        "1" * 65,
    ],
)
def test_invalid_prices_are_rejected_without_rounding(price):
    with pytest.raises(ValidationError):
        ProductCreation(name="Filtro", price=price)


@pytest.mark.parametrize(
    "field",
    [
        "tenant_id",
        "role",
        "actor_membership_id",
        "stock",
        "cost",
        "is_active",
        "id",
        "created_at",
    ],
)
def test_client_cannot_supply_authority_or_server_fields(field):
    with pytest.raises(ValidationError):
        ProductCreation(name="Filtro", price="1.00", **{field: "forbidden"})


@pytest.mark.parametrize("name", ["", "   ", "x" * 201, "\x00", "\ud800", 123])
def test_invalid_names_rejected(name):
    with pytest.raises(ValidationError):
        CategoryCreation(name=name)


@pytest.mark.parametrize("sku", ["", "   ", "x" * 101, "\x00", "\ud800", 123])
def test_invalid_sku_rejected(sku):
    with pytest.raises(ValidationError):
        ProductCreation(name="Filtro", price="1", sku=sku)


def test_optional_sku_category_and_small_decimal_context():
    with localcontext() as context:
        context.prec = 2
        data = ProductCreation(name="Filtro", price="999999999999999999.99")
    assert data.price == Decimal("999999999999999999.99")
    assert data.sku is None and data.category_id is None


@pytest.mark.parametrize("limit", [0, 101, -1, True, 1.0, "01", "+1", " 1", "1.0"])
def test_page_rejects_unbounded_or_ambiguous_limits(limit):
    with pytest.raises(ValidationError):
        CatalogPageQuery(limit=limit)


def test_page_allows_canonical_query_string_but_not_authority_fields():
    assert CatalogPageQuery(limit="100").limit == 100
    with pytest.raises(ValidationError):
        CatalogPageQuery(tenant_id="forbidden")


@pytest.mark.parametrize("value", ["x" * 201, "\x00", "\ud800", 123, True])
def test_product_search_rejects_invalid_or_unbounded_text(value):
    from app.modules.catalog.inputs import ProductSearchQuery

    with pytest.raises(ValidationError):
        ProductSearchQuery(q=value)


def test_product_search_normalizes_empty_and_rejects_authority():
    from app.modules.catalog.inputs import ProductSearchQuery

    assert ProductSearchQuery(q="  filtro  ").q == "filtro"
    assert ProductSearchQuery(q="   ").q is None
    with pytest.raises(ValidationError):
        ProductSearchQuery(q="filtro", tenant_id="foreign")
    with pytest.raises(ValidationError):
        CatalogPageQuery(q="filtro")


def test_product_category_filter_is_uuid_and_never_tenant_authority():
    from uuid import uuid4

    from app.modules.catalog.inputs import ProductSearchQuery

    category = uuid4()
    assert (
        ProductSearchQuery(category_id=str(category), q="filtro").category_id
        == category
    )
    with pytest.raises(ValidationError):
        ProductSearchQuery(category_id="all")
    with pytest.raises(ValidationError):
        ProductSearchQuery(category_id=str(category), tenant_id=str(uuid4()))
