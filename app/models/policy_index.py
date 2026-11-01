"""Versões completas e configuração de indexação do manual."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PolicyIndex(Base):
    __tablename__ = "policy_indexes"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_policy_indexes_fingerprint"),
        Index(
            "uq_policy_indexes_active", "is_active", unique=True, postgresql_where=text("is_active")
        ),
    )

    index_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    fingerprint: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(Text)
    document_version: Mapped[str] = mapped_column(Text)
    processing_config: Mapped[dict] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
