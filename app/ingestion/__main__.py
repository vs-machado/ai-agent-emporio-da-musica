"""Executa a carga explícita dos CSVs de demonstração."""

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from app.config import Settings
from app.database import create_database_engine
from app.ingestion.loader import DEFAULT_DATA_DIRECTORY, IngestionError, ingest


def main() -> int:
    parser = argparse.ArgumentParser(description="Carrega os CSVs operacionais no PostgreSQL.")
    parser.add_argument(
        "--data-dir", type=Path, default=DEFAULT_DATA_DIRECTORY, help="Diretório dos seis CSVs"
    )
    args = parser.parse_args()
    try:
        settings = Settings()
    except ValidationError:
        print("Configuração inválida: verifique as variáveis POSTGRES_*.", file=sys.stderr)
        return 1
    engine = create_database_engine(settings)
    try:
        counts = ingest(engine, args.data_dir)
    except IngestionError as exc:
        print(f"Erro de ingestão: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print("Carga concluída (registros processados por tabela):")
    for name, count in counts.items():
        print(f"  {name}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
