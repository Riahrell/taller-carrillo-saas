"""Administrative desired values; never tenant, stock or sale authority."""

import re
from decimal import Decimal, InvalidOperation
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CategoryCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=200, strict=True)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Invalid catalog name")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Invalid catalog name") from None
        return value


class ProductCreation(CategoryCreation):
    category_id: UUID | None = None
    sku: str | None = Field(default=None, min_length=1, max_length=100, strict=True)
    price: Decimal

    @field_validator("sku")
    @classmethod
    def clean_sku(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = cls.clean_name(value)
        return value

    @field_validator("price", mode="before")
    @classmethod
    def exact_price(cls, value: object) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
            raise ValueError("Price must be an exact decimal")
        if isinstance(value, str) and (not value or len(value) > 64):
            raise ValueError("Invalid price representation")
        try:
            price = Decimal(value)
        except (InvalidOperation, ValueError):
            raise ValueError("Invalid price") from None
        if (
            not price.is_finite()
            or price.as_tuple().exponent < -2
            or price < 0
            or price > Decimal("999999999999999999.99")
        ):
            raise ValueError("Price outside NUMERIC(20,2) bounds or precision")
        return price


class CatalogPageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    limit: int = Field(default=25, ge=1, le=100, strict=True)
    after: UUID | None = None

    @field_validator("limit", mode="before")
    @classmethod
    def canonical_limit(cls, value: object) -> object:
        if isinstance(value, str):
            if re.fullmatch(r"[1-9][0-9]{0,2}", value, flags=re.ASCII) is None:
                raise ValueError("Invalid page limit")
            return int(value)
        return value


class ProductSearchQuery(CatalogPageQuery):
    category_id: UUID | None = None
    q: str | None = Field(default=None, max_length=200, strict=True)

    @field_validator("q")
    @classmethod
    def clean_search(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if "\x00" in value:
            raise ValueError("Invalid product search")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Invalid product search") from None
        return value or None


class ProductChange(ProductCreation):
    is_active: bool = Field(strict=True)
