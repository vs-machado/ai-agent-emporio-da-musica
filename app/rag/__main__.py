"""Comando explícito de inspeção ou indexação do manual."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.database import create_database_engine
from app.rag.errors import PolicyIndexingError
from app.rag.evaluation import evaluate
from app.rag.indexing import DEFAULT_MANUAL_PATH, index_manual
from app.rag.parser import build_nodes, parse_manual, token_count
from app.rag.retrieval import LlamaIndexPolicyRetriever
from app.schemas.policies import PolicySearch
from app.services.errors import PolicyRetrievalError
from app.services.policies import PolicyService


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspeciona ou indexa o manual de políticas.")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_MANUAL_PATH, help="Manual em PDF")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inspect", action="store_true", help="Extrai sem banco nem embeddings")
    mode.add_argument("--query", help="Consulta o índice ativo sem gerar resposta por LLM")
    mode.add_argument(
        "--evaluate", action="store_true", help="Avalia cobertura de seções esperadas"
    )
    args = parser.parse_args()
    try:
        if args.inspect:
            manual = parse_manual(args.pdf.read_bytes())
            nodes = build_nodes(manual, args.pdf.name, "inspection", 1000)
            print(f"Versão {manual.version}: {len(manual.sections)} seções, {len(nodes)} chunks.")
            for section in manual.sections:
                print(
                    f"{section.number} — {section.title}: páginas "
                    f"{section.page_start}–{section.page_end}, {token_count(section.text)} tokens"
                )
            return 0
        settings = Settings()
        engine = create_database_engine(settings)
        try:
            if args.evaluate:
                report = evaluate(LlamaIndexPolicyRetriever(engine, settings))
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 0
            if args.query:
                service = PolicyService(LlamaIndexPolicyRetriever(engine, settings))
                response = service.search_policy(PolicySearch(query=args.query))
                print(json.dumps(response, ensure_ascii=False, indent=2))
                return 1 if response["status"] == "unavailable" else 0
            result = index_manual(engine, settings, args.pdf)
        finally:
            engine.dispose()
        print(
            f"Índice {result.index_id}: {result.status}; "
            f"{result.section_count} seções, {result.chunk_count} chunks."
        )
    except (PolicyIndexingError, OSError) as exc:
        message = (
            str(exc) if isinstance(exc, PolicyIndexingError) else "Não foi possível ler o PDF."
        )
        print(message, file=sys.stderr)
        return 1
    except (ValidationError, SQLAlchemyError, PolicyRetrievalError):
        print(
            "Falha de configuração ou banco; confira o ambiente e as migrations.", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
