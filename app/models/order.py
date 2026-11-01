"""Pedido de compra de um cliente."""

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Order(Base):
    """Pedido de compra de um cliente.

    O total é preservado da origem e não deve ser recalculado com preços
    atuais do catálogo. A previsão de entrega não representa a data
    real de recebimento.
    """

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("total_brl >= 0", name="nonnegative_total"),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled')",
            name="valid_status",
        ),
        CheckConstraint(
            "payment_method IN ('pix', 'boleto', 'debit', 'credit_3x', 'credit_6x', 'credit_12x')",
            name="valid_payment_method",
        ),
    )

    order_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.customer_id"), index=True)
    order_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text)
    total_brl: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    payment_method: Mapped[str] = mapped_column(Text)
    tracking_code: Mapped[str | None] = mapped_column(Text)
    estimated_delivery: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
