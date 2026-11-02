"""Contratos de pedidos, simulação de reembolso e registro de reclamações."""

from typing import Annotated
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import Email, Identifier, Input


class OrderReference(Input):
    order_id: Identifier | None = Field(
        default=None,
        description="ID opcional; omita para consultar os pedidos da conta autenticada",
    )
    email: Email | None = Field(
        default=None,
        description="E-mail opcional para conferência; acesso depende da conta autenticada",
    )


class OrderLookup(OrderReference):
    limit: Annotated[int, Field(strict=True, ge=1, le=20)] = Field(
        default=20, description="Quantidade de pedidos por página quando não houver order_id"
    )
    offset: Annotated[int, Field(strict=True, ge=0, le=10000)] = Field(
        default=0, description="Deslocamento para consultar outras páginas de pedidos"
    )


class RefundItem(Input):
    product_id: Identifier = Field(description="ID do produto presente no pedido consultado")
    quantity: Identifier = Field(
        description="Quantidade desse produto que o cliente deseja devolver"
    )


class RefundQuote(OrderReference):
    order_id: Identifier = Field(
        description="ID do pedido retornado por get_order_status; não exigir número do cliente"
    )
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
