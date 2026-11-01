"""Promoção associada a um produto."""

from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Promotion(Base):
    __tablename__ = "promotions"
    __table_args__ = (CheckConstraint("discount_percent BETWEEN 0 AND 100", name="valid_discount"),)

    promotion_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.product_id"), index=True)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    description: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool]
