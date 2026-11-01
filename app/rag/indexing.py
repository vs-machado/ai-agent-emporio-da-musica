"""Indexação idempotente e ativação atômica de versões do manual."""

import hashlib
import json
import math
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.schema import MetadataMode
from llama_index.core.vector_stores.utils import node_to_metadata_dict
from sqlalchemy import Engine, text, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PolicyIndex, PolicySection, PolicyVector
from app.rag.embeddings import build_embeddings
from app.rag.errors import PolicyIndexingError
from app.rag.parser import PARSER_VERSION, TOKEN_ENCODING, build_nodes, parse_manual

DEFAULT_MANUAL_PATH = Path(__file__).resolve().parents[2] / "kb" / "políticas_da_loja.pdf"


@dataclass(frozen=True)
class IndexingResult:
    index_id: UUID
    status: str
    section_count: int
    chunk_count: int


def processing_config(settings: Settings) -> dict:
    return {
        "parser": PARSER_VERSION,
        "pypdf": version("pypdf"),
        "llama_index_core": version("llama-index-core"),
        "google_genai_integration": version("llama-index-embeddings-google-genai"),
        "token_encoding": TOKEN_ENCODING,
        "chunk_tokens": settings.rag_chunk_tokens,
        "embedding_model": settings.rag_embedding_model,
        "embedding_dimensions": settings.rag_embedding_dimensions,
        "document_task": "RETRIEVAL_DOCUMENT",
        "query_task": "RETRIEVAL_QUERY",
    }


def activate(session: Session, index_id: UUID) -> None:
    session.execute(update(PolicyIndex).where(PolicyIndex.is_active).values(is_active=False))
    session.execute(
        update(PolicyIndex).where(PolicyIndex.index_id == index_id).values(is_active=True)
    )


def index_manual(
    engine: Engine,
    settings: Settings,
    path: Path = DEFAULT_MANUAL_PATH,
    embed_model: BaseEmbedding | None = None,
) -> IndexingResult:
    """Valida e vetoriza a fonte antes de substituir o índice ativo.

    Args:
        engine: Banco com as migrations aplicadas.
        settings: Modelo, dimensão e limite de tokens por chunk.
        path: Manual original em PDF.
        embed_model: Implementação LlamaIndex injetável para testes sem provedor.

    Returns:
        Identificador, contagens e estado indexed, unchanged ou reactivated.

    Raises:
        PolicyIndexingError: Fonte inválida ou embeddings indisponíveis/inconsistentes.
        SQLAlchemyError: Persistência falhou; a versão ativa anterior permanece intacta.
    """
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise PolicyIndexingError("Não foi possível ler o manual de políticas.") from exc
    manual = parse_manual(content)
    config = processing_config(settings)
    source_hash = hashlib.sha256(content).hexdigest()
    fingerprint = hashlib.sha256(
        json.dumps(
            {"source": path.name, "source_hash": source_hash, "config": config}, sort_keys=True
        ).encode()
    ).hexdigest()
    index_id = uuid5(NAMESPACE_URL, fingerprint)
    nodes = build_nodes(manual, path.name, str(index_id), settings.rag_chunk_tokens)
    with Session(engine) as session:
        existing = session.get(PolicyIndex, index_id)
        if existing and existing.is_active:
            return IndexingResult(index_id, "unchanged", len(manual.sections), len(nodes))
        needs_embeddings = existing is None
    if needs_embeddings:
        try:
            embeddings = (embed_model or build_embeddings(settings)).get_text_embedding_batch(
                [node.get_content(MetadataMode.EMBED) for node in nodes]
            )
        except Exception as exc:
            raise PolicyIndexingError(
                "Falha ao gerar embeddings; confira a chave e a disponibilidade do provedor."
            ) from exc
        if len(embeddings) != len(nodes) or any(
            len(vector) != settings.rag_embedding_dimensions
            or not all(math.isfinite(value) for value in vector)
            or not any(vector)
            for vector in embeddings
        ):
            raise PolicyIndexingError("O provedor retornou embeddings inválidos ou incompletos.")
        for node, embedding in zip(nodes, embeddings, strict=True):
            node.embedding = embedding
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT pg_advisory_xact_lock(73421, 1)"))
        existing = session.get(PolicyIndex, index_id)
        if existing:
            status = "unchanged" if existing.is_active else "reactivated"
        else:
            session.add(
                PolicyIndex(
                    index_id=index_id,
                    fingerprint=fingerprint,
                    source=path.name,
                    source_hash=source_hash,
                    document_version=manual.version,
                    processing_config=config,
                    is_active=False,
                )
            )
            session.flush()
            session.add_all(
                [
                    PolicySection(
                        index_id=index_id,
                        section=s.number,
                        title=s.title,
                        hierarchy=s.hierarchy,
                        parent_section=s.parent,
                        text=s.text,
                        page_start=s.page_start,
                        page_end=s.page_end,
                    )
                    for s in manual.sections
                ]
            )
            session.add_all(
                [
                    PolicyVector(
                        node_id=n.node_id,
                        text=n.text,
                        embedding=n.embedding,
                        metadata_=node_to_metadata_dict(n, remove_text=True),
                    )
                    for n in nodes
                ]
            )
            session.flush()
            status = "indexed"
        activate(session, index_id)
    return IndexingResult(index_id, status, len(manual.sections), len(nodes))
