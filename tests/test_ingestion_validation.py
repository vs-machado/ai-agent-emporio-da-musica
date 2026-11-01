"""Validações das fontes antes de qualquer gravação."""

from decimal import Decimal

import pytest

from app.ingestion.loader import DEFAULT_DATA_DIRECTORY, IngestionError, load_records


def test_original_sources_are_valid():
    records = load_records(DEFAULT_DATA_DIRECTORY)
    assert {name: len(rows) for name, rows in records.items()} == {
        "categories": 9,
        "customers": 50,
        "products": 65,
        "orders": 20,
        "order_items": 22,
        "promotions": 25,
    }
    assert records["products"][0].values["price_brl"] == Decimal("599.9")
    assert records["products"][0].values["specs"]["strings"] == "nylon"
    assert records["orders"][8].values["tracking_code"] is None


@pytest.mark.parametrize(
    ("source", "field", "value"),
    [
        ("products", "specs", "{broken"),
        ("products", "specs", "[]"),
        ("products", "created_at", "2026-02-30"),
        ("products", "price_brl", "1.234"),
        ("products", "price_brl", "NaN"),
        ("products", "stock_quantity", "-1"),
        ("products", "status", "unknown"),
        ("promotions", "is_active", "true"),
        ("promotions", "discount_percent", "101"),
        ("order_items", "quantity", "0"),
        ("customers", "email", "sensitive-invalid-email"),
        ("customers", "name", "   "),
    ],
)
def test_invalid_fields_report_location_without_values(
    data_directory, edit_csv, source, field, value
):
    edit_csv(source, lambda rows: rows[0].update({field: value}))
    with pytest.raises(IngestionError) as error:
        load_records(data_directory)
    assert source in str(error.value)
    assert "linha 2" in str(error.value)
    assert field in str(error.value)
    assert "sensitive-invalid-email" not in str(error.value)


def test_duplicate_key_is_rejected(data_directory, edit_csv):
    edit_csv("order_items", lambda rows: rows.append(rows[0].copy()))
    with pytest.raises(IngestionError, match="chave duplicada"):
        load_records(data_directory)


def test_missing_reference_is_rejected(data_directory, edit_csv):
    edit_csv("products", lambda rows: rows[0].update(category_id="999"))
    with pytest.raises(IngestionError, match="category_id não referencia"):
        load_records(data_directory)


def test_empty_source_is_rejected(data_directory, edit_csv):
    edit_csv("categories", lambda rows: rows.clear())
    with pytest.raises(IngestionError, match="sem registros"):
        load_records(data_directory)


def test_wrong_header_is_rejected(data_directory):
    path = data_directory / "desafio_tecnico_ai_eng - customers.csv"
    path.write_text("customer_id,name\n1,Cliente\n", encoding="utf-8")
    with pytest.raises(IngestionError, match="cabeçalho inválido"):
        load_records(data_directory)


def test_missing_column_is_rejected(data_directory):
    path = data_directory / "desafio_tecnico_ai_eng - categories.csv"
    path.write_text("category_id,name,description\n1,Nome\n", encoding="utf-8")
    with pytest.raises(IngestionError, match="quantidade de colunas"):
        load_records(data_directory)
