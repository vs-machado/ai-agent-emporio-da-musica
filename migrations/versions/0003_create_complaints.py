"""Cria registros de reclamações com chave de idempotência.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "complaints",
        sa.Column("complaint_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("contact_email", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("complaint_id", name="pk_complaints"),
        sa.UniqueConstraint("request_id", name="uq_complaints_request_id"),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.order_id"], name="fk_complaints_order_id_orders"
        ),
        sa.CheckConstraint("status IN ('open', 'closed')", name=op.f("ck_complaints_valid_status")),
    )
    op.create_index("ix_complaints_order_id", "complaints", ["order_id"])


def downgrade() -> None:
    op.drop_table("complaints")
