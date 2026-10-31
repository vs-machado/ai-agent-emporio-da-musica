"""Habilita pgvector para recuperação de políticas.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # A extensão pode ser compartilhada por outros componentes do banco.
    pass
