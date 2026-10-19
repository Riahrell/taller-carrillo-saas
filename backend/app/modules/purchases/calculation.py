"""Pure purchase valuation; no authorization, stock writes or payment effects."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from uuid import UUID

from app.modules.inventory.valuation import StockValue
from app.modules.purchases.inputs import PurchaseCreation

MAX_AMOUNT = Decimal("999999999999999999.99")
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class PurchaseLineAmounts:
    product_id: UUID
    quantity: Decimal
    unit_cost: Decimal
    stock_value: Decimal
    amount: Decimal


@dataclass(frozen=True)
class PurchaseAmounts:
    lines: tuple[PurchaseLineAmounts, ...]
    total: Decimal


def calculate_purchase(purchase: PurchaseCreation) -> PurchaseAmounts:
    # Revalidate internal copies too: model_copy/update and model_construct can
    # bypass Pydantic validation. Never trust precomputed line totals.
    purchase = PurchaseCreation.model_validate(purchase.model_dump())
    lines = []
    with localcontext(Context(prec=80, rounding=ROUND_HALF_UP)):
        total = Decimal(0)
        for line in purchase.lines:
            # Reuse the inventory rounding rule; do not implement receipt cost
            # arithmetic a second time. This is an entry value, not a new balance.
            entry = StockValue(Decimal(0), Decimal(0)).receive(
                line.quantity, line.unit_cost
            )
            # Monetary lines round the exact product directly to cents. Rounding
            # the six-decimal stock value first would introduce double rounding.
            amount = (line.quantity * line.unit_cost).quantize(_CENT)
            if amount > MAX_AMOUNT:
                raise ValueError("Purchase line exceeds monetary capacity")
            total += amount
            if total > MAX_AMOUNT:
                raise ValueError("Purchase exceeds monetary capacity")
            lines.append(
                PurchaseLineAmounts(
                    line.product_id,
                    line.quantity,
                    line.unit_cost,
                    entry.value,
                    amount,
                )
            )
        return PurchaseAmounts(tuple(lines), total.quantize(_CENT))
