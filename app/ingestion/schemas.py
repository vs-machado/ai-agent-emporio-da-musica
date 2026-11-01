"""Validação e conversão das linhas dos arquivos de origem."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, Json, JsonValue, field_validator

Identifier = Annotated[int, Field(gt=0, le=2_147_483_647)]
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
RequiredText = Annotated[str, Field(min_length=1)]


class CsvRow(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def reject_blank_text(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("Campo obrigatório vazio")
        return value


class CategoryRow(CsvRow):
    category_id: Identifier
    name: RequiredText
    description: RequiredText


class CustomerRow(CsvRow):
    customer_id: Identifier
    name: RequiredText
    phone: RequiredText
    email: Annotated[str, Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")]
    city: RequiredText


class ProductRow(CsvRow):
    product_id: Identifier
    price_brl: Money
    name: RequiredText
    category_id: Identifier
    description: RequiredText
    stock_quantity: Annotated[int, Field(ge=0, le=2_147_483_647)]
    status: Literal["active", "discontinued", "coming_soon"]
    specs: Json[dict[str, JsonValue]]
    created_at: date


class OrderRow(CsvRow):
    order_id: Identifier
    customer_id: Identifier
    order_date: date
    status: Literal["pending", "confirmed", "shipped", "delivered", "cancelled"]
    total_brl: Money
    payment_method: Literal["pix", "boleto", "debit", "credit_3x", "credit_6x", "credit_12x"]
    tracking_code: str | None
    estimated_delivery: date | None
    notes: str | None

    @field_validator("tracking_code", "estimated_delivery", "notes", mode="before")
    @classmethod
    def empty_to_none(cls, value):
        return None if value == "" else value


class OrderItemRow(CsvRow):
    order_id: Identifier
    quantity: Identifier
    product_id: Identifier


class PromotionRow(CsvRow):
    promotion_id: Identifier
    product_id: Identifier
    discount_percent: Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]
    description: RequiredText
    is_active: bool

    @field_validator("is_active", mode="before")
    @classmethod
    def parse_active_flag(cls, value):
        if value not in ("0", "1"):
            raise ValueError("Use 0 ou 1")
        return value == "1"
