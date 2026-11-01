"""Tabela vetorial compatível com o PGVectorStore do LlamaIndex."""

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Computed, Index, String
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PolicyVector(Base):
    __tablename__ = "data_policy_vectors"
    __table_args__ = (Index("ix_policy_vectors_search", "text_search_tsv", postgresql_using="gin"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String, unique=True)
    text: Mapped[str] = mapped_column(String)
    metadata_: Mapped[dict] = mapped_column(JSONB)
    embedding: Mapped[list[float]] = mapped_column(Vector(768))
    text_search_tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('portuguese', text)", persisted=True)
    )
