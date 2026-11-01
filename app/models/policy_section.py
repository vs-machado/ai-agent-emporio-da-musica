"""Unidades estruturais do manual para expansão e referências."""

from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PolicySection(Base):
    __tablename__ = "policy_sections"
    __table_args__ = (
        UniqueConstraint("index_id", "section", name="uq_policy_sections_index_section"),
        CheckConstraint("page_start >= 1 AND page_end >= page_start", name="valid_pages"),
    )

    section_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    index_id: Mapped[UUID] = mapped_column(ForeignKey("policy_indexes.index_id"), index=True)
    section: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    hierarchy: Mapped[list[str]] = mapped_column(JSONB)
    parent_section: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int]
    page_end: Mapped[int]
