"""Contratos de pedidos, simulação de reembolso e registro de reclamações."""

from typing import Annotated
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import Email, Identifier, Input


class OrderLookup(Input):
    order_id: Identifier = Field(description="Número do pedido informado pelo cliente")
    email: Email = Field(
        description="E-mail cadastrado informado pelo cliente para verificar o pedido"
    )


class RefundItem(Input):
    product_id: Identifier = Field(description="ID do produto presente no pedido consultado")
    quantity: Identifier = Field(
        description="Quantidade desse produto que o cliente deseja devolver"
    )


class RefundQuote(OrderLookup):
    items: Annotated[list[RefundItem], Field(min_length=1, max_length=20)] | None = Field(
        default=None,
        description=(
            "Produtos e quantidades a devolver. Omitir ou usar null somente para simular "
            "a devolução integral do pedido, conforme solicitado pelo cliente"
        ),
    )

    @model_validator(mode="after")
    def reject_duplicate_products(self):
        if self.items is not None:
            ids = [item.product_id for item in self.items]
            if len(set(ids)) != len(ids):
                raise ValueError("Agrupe a quantidade de cada produto em um único item")
        return self


class ComplaintRegistration(Input):
    request_id: UUID = Field(
        description="Chave única da solicitação; reutilizar a mesma em retries"
    )
    contact_email: Email = Field(description="E-mail fornecido pelo cliente para contato")
    message: Annotated[str, Field(min_length=10, max_length=4000)] = Field(
        description="Relato do cliente sem prometer solução"
    )
    order_id: Identifier | None = Field(
        default=None, description="Pedido relacionado, se houver; será verificado pelo e-mail"
    )
