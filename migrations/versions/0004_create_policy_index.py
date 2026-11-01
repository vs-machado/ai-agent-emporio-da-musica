"""Cria versões do manual, seções e vetores compatíveis com LlamaIndex.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_indexes",
        sa.Column("index_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.Text(), nullable=False),
        sa.Column("document_version", sa.Text(), nullable=False),
        sa.Column("processing_config", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("index_id", name="pk_policy_indexes"),
        sa.UniqueConstraint("fingerprint", name="uq_policy_indexes_fingerprint"),
    )
    op.create_index(
        "uq_policy_indexes_active",
        "policy_indexes",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "policy_sections",
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("index_id", sa.Uuid(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("hierarchy", postgresql.JSONB(), nullable=False),
        sa.Column("parent_section", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("section_id", name="pk_policy_sections"),
        sa.ForeignKeyConstraint(
            ["index_id"],
            ["policy_indexes.index_id"],
            name="fk_policy_sections_index_id_policy_indexes",
        ),
        sa.UniqueConstraint("index_id", "section", name="uq_policy_sections_index_section"),
        sa.CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name=op.f("ck_policy_sections_valid_pages"),
        ),
    )
    op.create_index("ix_policy_sections_index_id", "policy_sections", ["index_id"])
    op.create_table(
        "data_policy_vectors",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("metadata_", postgresql.JSONB(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column(
            "text_search_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('portuguese', text)", persisted=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_policy_vectors"),
        sa.UniqueConstraint("node_id", name="uq_data_policy_vectors_node_id"),
    )
    op.create_index(
        "ix_policy_vectors_search",
        "data_policy_vectors",
        ["text_search_tsv"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_table("data_policy_vectors")
    op.drop_table("policy_sections")
    op.drop_table("policy_indexes")
