"""Comportamento da ingestão em PostgreSQL real."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.ingestion.loader import SOURCES, IngestionError, ingest
from app.models import Category, Customer, Order, Product, Promotion


def test_load_and_repeat_preserve_values(engine):
    expected = {
        "categories": 9,
        "customers": 50,
        "products": 65,
        "orders": 20,
        "order_items": 22,
        "promotions": 25,
    }
    assert ingest(engine) == expected
    assert ingest(engine) == expected
    with Session(engine) as session:
        for source in SOURCES:
            assert (
                session.scalar(select(func.count()).select_from(source.model))
                == expected[source.name]
            )
        assert session.get(Product, 81).price_brl == Decimal("599.90")
        assert session.get(Product, 81).specs["strings"] == "nylon"
        assert session.get(Product, 145).specs["strings"] == "6"
        assert session.get(Product, 136).stock_quantity == 13
        assert session.get(Product, 136).status == "discontinued"
        assert session.get(Order, 3).total_brl == Decimal("3450.00")
        assert session.get(Order, 20).total_brl == Decimal("1400.00")
        order = session.get(Order, 9)
        assert (order.tracking_code, order.estimated_delivery, order.notes) == (None, None, None)
        assert session.get(Promotion, 1).is_active is False
        assert session.get(Promotion, 12).is_active is True


def test_updates_existing_records_without_deleting_others(engine, data_directory, edit_csv):
    ingest(engine)
    with Session(engine) as session, session.begin():
        session.add(
            Customer(customer_id=999, name="Extra", phone="0", email="extra@test.com", city="X")
        )
    edit_csv("products", lambda rows: rows[0].update(price_brl="620.15", stock_quantity="3"))
    ingest(engine, data_directory)
    with Session(engine) as session:
        assert session.get(Product, 81).price_brl == Decimal("620.15")
        assert session.get(Product, 81).stock_quantity == 3
        assert session.get(Customer, 999) is not None


def test_validation_failure_leaves_database_untouched(engine, data_directory, edit_csv):
    ingest(engine)
    edit_csv("categories", lambda rows: rows[0].update(name="Não deve persistir"))
    edit_csv("promotions", lambda rows: rows[0].update(product_id="999"))
    with pytest.raises(IngestionError, match="product_id não referencia"):
        ingest(engine, data_directory)
    with Session(engine) as session:
        assert session.get(Category, 1).name == "Guitarras"


def test_database_failure_rolls_back_preceding_tables(engine, data_directory, edit_csv):
    ingest(engine)
    edit_csv("categories", lambda rows: rows[0].update(name="Não deve persistir"))
    edit_csv("promotions", lambda rows: rows[0].update(description="force database failure"))
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE promotions ADD CONSTRAINT test_ingestion_failure "
                "CHECK (description != 'force database failure')"
            )
        )
    try:
        with pytest.raises(IngestionError, match="carga revertida") as error:
            ingest(engine, data_directory)
        assert "force database failure" not in str(error.value)
        with Session(engine) as session:
            assert session.get(Category, 1).name == "Guitarras"
            assert session.get(Promotion, 1).description == "Black Friday"
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE promotions DROP CONSTRAINT test_ingestion_failure")
            )
