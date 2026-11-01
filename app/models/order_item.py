"""Item de pedido identificado pelo pedido e pelo produto."""

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OrderItem(Base):
    """Item identificado pela chave composta de pedido e produto.

    A fonte não fornece preço unitário histórico; esse valor não deve ser
    inferido a partir do preço atual do produto.
    """

    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="positive_quantity"),)

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.order_id"), primary_key=True, autoincrement=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.product_id"), primary_key=True, autoincrement=False, index=True
    )
    quantity: Mapped[int]
