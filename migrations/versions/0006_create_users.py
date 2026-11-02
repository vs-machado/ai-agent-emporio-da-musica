"""Contas de acesso e propriedade das conversas.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.customer_id"), unique=True),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )
    # Conversas anônimas antigas permanecem sem dono e não são adotadas por novas contas.
    op.add_column("conversations", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_conversations_user_id_users", "conversations", "users", ["user_id"], ["user_id"]
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_table(
        "ui_sessions",
        sa.Column("session_hash", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=False),
    )
    op.create_index("ix_ui_sessions_user_id", "ui_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_table("ui_sessions")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_constraint("fk_conversations_user_id_users", "conversations", type_="foreignkey")
    op.drop_column("conversations", "user_id")
    op.drop_table("users")
