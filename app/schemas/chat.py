"""Contratos de entrada e resposta do atendimento, sem dependência da API HTTP."""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.schemas.base import Input


class ChatRequest(Input):
    message: Annotated[str, Field(min_length=1, max_length=4000)] = Field(
        description="Mensagem do cliente em texto, sem papéis de sistema ou resultados de tools"
    )
    request_id: UUID = Field(
        default_factory=uuid4,
        description="ID do turno; reutilizar em retries da mesma solicitação",
    )


class ChatAction(BaseModel):
    name: str
    status: str
    reference: str | None = None


class ChatResponse(BaseModel):
    request_id: UUID
    status: Literal["ok", "unavailable", "limit_reached"]
    message: str
    model: str | None = None
    used_fallback: bool = False
    actions: list[ChatAction] = Field(default_factory=list)
