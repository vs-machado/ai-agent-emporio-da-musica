"""Leitura validada e carga transacional dos dados operacionais."""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.ingestion.schemas import (
    CategoryRow,
    CsvRow,
    CustomerRow,
    OrderItemRow,
    OrderRow,
    ProductRow,
    PromotionRow,
)
from app.models import Base, Category, Customer, Order, OrderItem, Product, Promotion

DEFAULT_DATA_DIRECTORY = Path(__file__).resolve().parents[2] / "agent-data"


class IngestionError(Exception):
    """Erro de ingestão que pode ser exibido sem expor valores dos registros."""


@dataclass(frozen=True)
class Source:
    model: type[Base]
    schema: type[CsvRow]
    references: tuple[tuple[str, str, str], ...] = ()

    @property
    def name(self) -> str:
        return self.model.__tablename__

    @property
    def filename(self) -> str:
        return f"desafio_tecnico_ai_eng - {self.name}.csv"


SOURCES = (
    Source(Category, CategoryRow),
    Source(Customer, CustomerRow),
    Source(Product, ProductRow, (("category_id", "categories", "category_id"),)),
    Source(Order, OrderRow, (("customer_id", "customers", "customer_id"),)),
    Source(
        OrderItem,
        OrderItemRow,
        (("order_id", "orders", "order_id"), ("product_id", "products", "product_id")),
    ),
    Source(Promotion, PromotionRow, (("product_id", "products", "product_id"),)),
)


@dataclass
class Record:
    line: int
    values: dict[str, Any]


def validate_headers(headers: list[str] | None, source: Source) -> None:
    """Confere os campos esperados, sem exigir a ordem das colunas."""
    expected = set(source.schema.model_fields)
    if headers is None or len(headers) != len(expected) or set(headers) != expected:
        raise IngestionError(f"{source.filename}, linha 1: cabeçalho inválido")


def validate_row(row: dict, source: Source, line: int) -> dict[str, Any]:
    """Valida a estrutura e os valores sem expor dados nos erros."""
    if None in row or any(value is None for value in row.values()):
        raise IngestionError(f"{source.filename}, linha {line}: quantidade de colunas inválida")
    try:
        return source.schema.model_validate(row).model_dump()
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(map(str, error["loc"]))
            for error in exc.errors(include_input=False, include_context=False)
        )
        raise IngestionError(
            f"{source.filename}, linha {line}: campos inválidos: {fields}"
        ) from None


def read_source(directory: Path, source: Source) -> list[Record]:
    """Lê um CSV, valida suas linhas e rejeita chaves duplicadas.

    Args:
        directory: Diretório que contém os CSVs de origem.
        source: Define o arquivo, o schema de validação e o modelo da tabela.

    Returns:
        Registros validados, com os valores convertidos e a linha de origem.

    Raises:
        IngestionError: Se o arquivo não puder ser lido, estiver sem registros
            ou contiver cabeçalho, campos ou chaves inválidos.
    """
    records = []
    seen = set()
    keys = [column.name for column in source.model.__table__.primary_key]
    reader = None
    try:
        with (directory / source.filename).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            validate_headers(reader.fieldnames, source)
            for row in reader:
                line = reader.line_num
                values = validate_row(row, source, line)
                key = tuple(values[field] for field in keys)
                if key in seen:
                    raise IngestionError(
                        f"{source.filename}, linha {line}: chave duplicada ({', '.join(keys)})"
                    )
                seen.add(key)
                records.append(Record(line, values))
    except (OSError, UnicodeError, csv.Error):
        line = reader.line_num if reader else 1
        raise IngestionError(
            f"{source.filename}, linha {line}: não foi possível ler o CSV"
        ) from None
    if not records:
        raise IngestionError(f"{source.filename}: arquivo sem registros")
    return records


def load_records(directory: Path) -> dict[str, list[Record]]:
    """Valida os seis arquivos e suas referências antes de acessar o banco.

    Args:
        directory: Diretório que contém os seis CSVs com os nomes esperados.

    Returns:
        Registros validados agrupados pelo nome da tabela de destino.

    Raises:
        IngestionError: Se alguma fonte for inválida ou uma referência não
            existir nos arquivos desta carga, mesmo que já exista no banco.
    """
    data = {source.name: read_source(directory, source) for source in SOURCES}
    for source in SOURCES:
        for field, parent, parent_key in source.references:
            parent_ids = {record.values[parent_key] for record in data[parent]}
            for record in data[source.name]:
                if record.values[field] not in parent_ids:
                    raise IngestionError(
                        f"{source.filename}, linha {record.line}: "
                        f"{field} não referencia um registro de {parent}"
                    )
    return data


def ingest(engine: Engine, directory: Path = DEFAULT_DATA_DIRECTORY) -> dict[str, int]:
    """Insere ou atualiza pela chave primária em uma única transação.

    Os valores dos CSVs prevalecem sobre os registros existentes. Registros
    ausentes dos arquivos não são excluídos.

    Args:
        engine: Conexão SQLAlchemy ao PostgreSQL com as migrations aplicadas.
        directory: Diretório dos CSVs; por padrão, usa o agent-data do projeto.

    Returns:
        Quantidade de registros processados por tabela, sem distinguir
        inserções, atualizações ou registros sem mudanças.

    Raises:
        IngestionError: Se a validação falhar ou houver erro no banco.
            Erros na gravação revertem toda a transação da carga.
    """
    data = load_records(directory)
    source = SOURCES[0]
    try:
        with engine.begin() as connection:
            for source in SOURCES:
                table = source.model.__table__
                statement = insert(table).values([record.values for record in data[source.name]])
                statement = statement.on_conflict_do_update(
                    index_elements=list(table.primary_key.columns),
                    set_={
                        column.name: statement.excluded[column.name]
                        for column in table.columns
                        if not column.primary_key
                    },
                )
                connection.execute(statement)
    except SQLAlchemyError:
        raise IngestionError(
            f"{source.filename}: falha ao gravar no banco; carga revertida. "
            "Verifique a conexão e as migrations."
        ) from None
    return {name: len(records) for name, records in data.items()}
