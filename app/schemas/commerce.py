"""Contratos de carrinho e simulação de pagamento e frete."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.base import Identifier, Input, PostalCode, Text


class CartItem(Input):
    product_id: Identifier = Field(description="ID do produto no catálogo")
    quantity: Annotated[int, Field(strict=True, ge=1, le=100)] = Field(
        default=1, description="Quantidade desejada"
    )


class Cart(Input):
    items: Annotated[list[CartItem], Field(min_length=1, max_length=20)] = Field(
        description="Produtos e quantidades; não informar preços nem descontos"
    )

    @model_validator(mode="after")
    def reject_duplicate_products(self):
        ids = [item.product_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("Agrupe a quantidade de cada produto em um único item")
        return self


class PaymentQuote(Cart):
    payment_method: Literal["pix", "debit", "credit", "boleto", "combined"] = Field(
        description="Forma de pagamento; combined exige confirmação das condições pela equipe"
    )
    installments: Annotated[int, Field(strict=True, ge=1, le=12)] = Field(
        default=1, description="Número de parcelas no crédito"
    )

    @model_validator(mode="after")
    def validate_installments(self):
        if self.payment_method != "credit" and self.installments != 1:
            raise ValueError("Parcelamento é exclusivo do cartão de crédito")
        return self


class ShippingQuote(Cart):
    destination_city: Text | None = Field(
        default=None,
        description="Cidade de destino; sem CEP, deve ser informada junto com destination_state",
    )
    destination_state: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = Field(
        default=None,
        description="UF do destino. Obrigatória junto com a cidade quando não houver CEP",
    )
    destination_postal_code: PostalCode | None = Field(
        default=None, description="CEP do destino informado pelo cliente, consultado no ViaCEP"
    )

    @model_validator(mode="after")
    def validate_destination(self):
        if self.destination_postal_code is None and bool(self.destination_city) != bool(
            self.destination_state
        ):
            raise ValueError(
                "Sem destination_postal_code, informe destination_city e destination_state juntos"
            )
        return self
