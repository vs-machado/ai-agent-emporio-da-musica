"""Produto do catálogo e suas especificações."""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Date, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Product(Base):
    """Produto com especificações variáveis conforme a categoria.

    As especificações são preservadas sem normalização. Status comercial e
    estoque são independentes: estoque positivo não garante disponibilidade
    para venda.
    """

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price_brl >= 0", name="nonnegative_price"),
        CheckConstraint("stock_quantity >= 0", name="nonnegative_stock"),
        CheckConstraint("status IN ('active', 'discontinued', 'coming_soon')", name="valid_status"),
        CheckConstraint("jsonb_typeof(specs) = 'object'", name="specs_object"),
    )

    product_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    price_brl: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    name: Mapped[str] = mapped_column(Text)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.category_id"), index=True)
    description: Mapped[str] = mapped_column(Text)
    stock_quantity: Mapped[int]
    status: Mapped[str] = mapped_column(Text)
    specs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[date] = mapped_column(Date)
