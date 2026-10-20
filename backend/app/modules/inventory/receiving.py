"""Purchase stock entry inside the caller's authorized, tenant-locked transaction."""

from decimal import ROUND_HALF_UP, Context, Decimal, localcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.inventory.models import StockBalance, StockMovement
from app.modules.inventory.valuation import StockValue
from app.modules.purchases.models import (
    PurchaseItem,
    PurchaseReceipt,
    PurchaseReceiptLine,
)


def receive_purchase_item(
    session: Session, receipt: PurchaseReceipt, item: PurchaseItem
) -> PurchaseReceiptLine:
    """Caller owns tenant/product locks, authorization and commit; no external work."""
    if receipt.tenant_id != item.tenant_id or receipt.purchase_id != item.purchase_id:
        raise ValueError("Invalid receipt source")
    balance = session.scalar(
        select(StockBalance)
        .where(
            StockBalance.tenant_id == receipt.tenant_id,
            StockBalance.product_id == item.product_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    before = (
        StockValue(balance.quantity, balance.value)
        if balance
        else StockValue(Decimal(0), Decimal(0))
    )
    after = before.receive(item.quantity, item.unit_cost)
    with localcontext(Context(prec=80, rounding=ROUND_HALF_UP)):
        quantity_delta, value_delta = (
            after.quantity - before.quantity,
            after.value - before.value,
        )
    if quantity_delta != item.quantity or value_delta != item.stock_value:
        raise ValueError("Invalid receipt valuation")
    line = PurchaseReceiptLine(
        tenant_id=receipt.tenant_id,
        purchase_id=receipt.purchase_id,
        receipt_id=receipt.id,
        purchase_item_id=item.id,
        product_id=item.product_id,
        before_quantity=before.quantity,
        after_quantity=after.quantity,
        before_value=before.value,
        after_value=after.value,
        quantity_delta=quantity_delta,
        value_delta=value_delta,
    )
    session.add(line)
    session.flush()
    session.add(
        StockMovement(
            tenant_id=receipt.tenant_id,
            product_id=item.product_id,
            receipt_line_id=line.id,
            quantity_delta=quantity_delta,
            value_delta=value_delta,
            created_at=receipt.created_at,
        )
    )
    if balance is None:
        session.add(
            StockBalance(
                tenant_id=receipt.tenant_id,
                product_id=item.product_id,
                quantity=after.quantity,
                value=after.value,
            )
        )
    else:
        balance.quantity, balance.value = after.quantity, after.value
    session.flush()
    return line
