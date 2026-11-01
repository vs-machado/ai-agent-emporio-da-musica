"""Registro persistente de reclamações de atendimento."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Complaint(Base):
    """Reclamação registrada, sem implicar envio ou recebimento pela equipe.

    A chave de requisição evita duplicação em retries da mesma solicitação.
    O prazo de retorno depende de um processo humano ainda não integrado.
    """

    __tablename__ = "complaints"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_complaints_request_id"),
        CheckConstraint("status IN ('open', 'closed')", name="valid_status"),
    )

    complaint_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request_id: Mapped[UUID]
    contact_email: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.order_id"), index=True)
    status: Mapped[str] = mapped_column(Text, default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
