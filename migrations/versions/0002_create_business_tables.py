"""Cria tabelas dos dados operacionais.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("category_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("category_id", name="pk_categories"),
    )
    op.create_table(
        "customers",
        sa.Column("customer_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("customer_id", name="pk_customers"),
    )
    op.create_table(
        "products",
        sa.Column("product_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("price_brl", sa.Numeric(12, 2), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("stock_quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("specs", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.Date(), nullable=False),
        sa.CheckConstraint("price_brl >= 0", name=op.f("ck_products_nonnegative_price")),
        sa.CheckConstraint("stock_quantity >= 0", name=op.f("ck_products_nonnegative_stock")),
        sa.CheckConstraint(
            "status IN ('active', 'discontinued', 'coming_soon')",
            name=op.f("ck_products_valid_status"),
        ),
        sa.CheckConstraint("jsonb_typeof(specs) = 'object'", name=op.f("ck_products_specs_object")),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.category_id"],
            name="fk_products_category_id_categories",
        ),
        sa.PrimaryKeyConstraint("product_id", name="pk_products"),
    )
    op.create_index("ix_products_category_id", "products", ["category_id"])
    op.create_table(
        "orders",
        sa.Column("order_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("total_brl", sa.Numeric(12, 2), nullable=False),
        sa.Column("payment_method", sa.Text(), nullable=False),
        sa.Column("tracking_code", sa.Text(), nullable=True),
        sa.Column("estimated_delivery", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint("total_brl >= 0", name=op.f("ck_orders_nonnegative_total")),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled')",
            name=op.f("ck_orders_valid_status"),
        ),
        sa.CheckConstraint(
            "payment_method IN ('pix', 'boleto', 'debit', 'credit_3x', 'credit_6x', 'credit_12x')",
            name=op.f("ck_orders_valid_payment_method"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.customer_id"], name="fk_orders_customer_id_customers"
        ),
        sa.PrimaryKeyConstraint("order_id", name="pk_orders"),
    )
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])
    op.create_table(
        "order_items",
        sa.Column("order_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("product_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_order_items_positive_quantity")),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.order_id"], name="fk_order_items_order_id_orders"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.product_id"], name="fk_order_items_product_id_products"
        ),
        sa.PrimaryKeyConstraint("order_id", "product_id", name="pk_order_items"),
    )
    op.create_index("ix_order_items_product_id", "order_items", ["product_id"])
    op.create_table(
        "promotions",
        sa.Column("promotion_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "discount_percent BETWEEN 0 AND 100", name=op.f("ck_promotions_valid_discount")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.product_id"], name="fk_promotions_product_id_products"
        ),
        sa.PrimaryKeyConstraint("promotion_id", name="pk_promotions"),
    )
    op.create_index("ix_promotions_product_id", "promotions", ["product_id"])


def downgrade() -> None:
    for table in ("promotions", "order_items", "orders", "products", "customers", "categories"):
        op.drop_table(table)
