"""Conta de acesso, separada do cadastro comercial importado."""

from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.customer_id"), unique=True
    )
    token_version: Mapped[int] = mapped_column(default=0, server_default="0")


class UISession(Base):
    """Vincula também a fila/SSE do Gradio ao dono autenticado."""

    __tablename__ = "ui_sessions"

    session_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id"), index=True)
