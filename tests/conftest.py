"""Fixtures dos CSVs e do banco isolado de integração."""

import csv
import os
import shutil

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.ingestion.loader import DEFAULT_DATA_DIRECTORY


@pytest.fixture
def data_directory(tmp_path):
    return shutil.copytree(DEFAULT_DATA_DIRECTORY, tmp_path / "agent-data")


@pytest.fixture
def edit_csv(data_directory):
    def edit(name, transform):
        path = data_directory / f"desafio_tecnico_ai_eng - {name}.csv"
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames
            rows = list(reader)
        transform(rows)
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    return edit


@pytest.fixture(scope="session")
def database_engine():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Defina TEST_DATABASE_URL para executar testes de integração")
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL deve apontar para PostgreSQL com nome terminado em _test")
    with pytest.MonkeyPatch.context() as patch:
        for key, value in {
            "POSTGRES_DB": url.database,
            "POSTGRES_USER": url.username,
            "POSTGRES_PASSWORD": url.password,
            "POSTGRES_HOST": url.host,
            "POSTGRES_PORT": str(url.port or 5432),
        }.items():
            patch.setenv(key, value or "")
        command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def engine(database_engine):
    statement = text("TRUNCATE promotions, order_items, orders, products, customers, categories")
    with database_engine.begin() as connection:
        connection.execute(statement)
    try:
        yield database_engine
    finally:
        with database_engine.begin() as connection:
            connection.execute(statement)
