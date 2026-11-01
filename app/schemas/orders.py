"""Contratos de consulta de pedidos e registro de reclamações."""

from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.schemas.base import Email, Identifier, Input


class OrderLookup(Input):
    order_id: Identifier = Field(description="Número do pedido informado pelo cliente")
    email: Email = Field(
        description="E-mail cadastrado informado pelo cliente para verificar o pedido"
    )


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
