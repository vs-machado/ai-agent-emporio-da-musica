"""Nome de exibição das contas, preservando cadastros existentes.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("name", sa.String(200), nullable=True))
    op.execute(
        "UPDATE users SET name = LEFT(TRIM(customers.name), 200) "
        "FROM customers WHERE users.customer_id = customers.customer_id"
    )


def downgrade() -> None:
    op.drop_column("users", "name")
