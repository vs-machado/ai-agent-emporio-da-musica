"""Retrieval LlamaIndex com expansão estrutural e fallback textual PostgreSQL."""

import logging
from dataclasses import dataclass

from llama_index.core import VectorStoreIndex
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.core.vector_stores import MetadataFilter, MetadataFilters
from llama_index.vector_stores.postgres import PGVectorStore
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.models import PolicyIndex, PolicySection, PolicyVector
from app.rag.embeddings import build_embeddings
from app.rag.parser import token_count
from app.schemas.policies import PolicyPassage
from app.services.errors import PolicyRetrievalError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalResult:
    passages: list[PolicyPassage]
    mode: str
    candidate_count: int


class LlamaIndexPolicyRetriever:
    def __init__(
        self, engine: Engine, settings: Settings, embed_model: BaseEmbedding | None = None
    ):
        self.engine = engine
        self.settings = settings
        self.embed_model = embed_model
        self.vector_store = PGVectorStore(
            connection_string=engine.url.render_as_string(hide_password=False),
            async_connection_string=engine.url.render_as_string(hide_password=False),
            table_name="policy_vectors",
            schema_name="public",
            embed_dim=settings.rag_embedding_dimensions,
            use_jsonb=True,
            perform_setup=False,
            create_engine_kwargs={"poolclass": NullPool},
        )

    def _vector_search(self, query: str, index: PolicyIndex) -> list[NodeWithScore]:
        config = index.processing_config
        if (
            config["embedding_model"] != self.settings.rag_embedding_model
            or config["embedding_dimensions"] != self.settings.rag_embedding_dimensions
        ):
            raise PolicyRetrievalError("Modelo configurado difere do índice ativo.")
        if self.embed_model is None:
            self.embed_model = build_embeddings(self.settings)
        vector_index = VectorStoreIndex.from_vector_store(
            self.vector_store, embed_model=self.embed_model
        )
        retriever = vector_index.as_retriever(
            similarity_top_k=self.settings.rag_top_k,
            filters=MetadataFilters(
                filters=[MetadataFilter(key="index_id", value=str(index.index_id))]
            ),
        )
        return retriever.retrieve(query)

    def _text_search(self, query: str, index: PolicyIndex) -> list[NodeWithScore]:
        tsquery = func.websearch_to_tsquery("portuguese", query)
        rank = func.ts_rank_cd(PolicyVector.text_search_tsv, tsquery)
        statement = (
            select(PolicyVector, rank)
            .where(
                PolicyVector.metadata_["index_id"].astext == str(index.index_id),
                PolicyVector.text_search_tsv.op("@@")(tsquery),
            )
            .order_by(rank.desc(), PolicyVector.node_id)
            .limit(self.settings.rag_top_k)
        )
        with Session(self.engine) as session:
            return [
                NodeWithScore(
                    node=TextNode(id_=row.node_id, text=row.text, metadata=row.metadata_),
                    score=score,
                )
                for row, score in session.execute(statement)
            ]

    def search(self, query: str, limit: int = 5) -> list[PolicyPassage]:
        """Recupera evidências de uma única versão, sem gerar resposta ao cliente.

        Args:
            query: Pergunta usada para embedding ou busca textual em português.
            limit: Máximo de evidências finais, entre um e cinco.

        Returns:
            Unidades completas em ordem de relevância, dentro do orçamento de tokens.
            A busca vazia não é interpretada como confirmação de uma política.

        Raises:
            PolicyRetrievalError: Nenhum índice completo está ativo.
            SQLAlchemyError: O banco está indisponível inclusive para o fallback.
        """
        return self.retrieve(query, limit).passages

    def retrieve(self, query: str, limit: int = 5) -> RetrievalResult:
        """Inclui diagnóstico por consulta para avaliação, sem estado mutável compartilhado."""
        if not 1 <= limit <= 5:
            raise ValueError("O limite de evidências deve estar entre um e cinco")
        with Session(self.engine) as session:
            index = session.scalar(select(PolicyIndex).where(PolicyIndex.is_active))
            if index is None:
                raise PolicyRetrievalError("Nenhuma versão do manual está ativa.")
        try:
            candidates = self._vector_search(query, index)
            mode = "vector"
        except Exception:
            logger.warning("Busca vetorial de políticas indisponível; usando fallback textual.")
            candidates = self._text_search(query, index)
            mode = "text_fallback"
        return RetrievalResult(
            self._select_evidence(candidates, index, limit), mode, len(candidates)
        )

    def _select_evidence(
        self, candidates: list[NodeWithScore], index: PolicyIndex, limit: int
    ) -> list[PolicyPassage]:
        """Expande para a subseção se couber, sem cortar regras para preencher o orçamento."""
        if not candidates:
            return []
        section_numbers = {candidate.node.metadata["section"] for candidate in candidates}
        with Session(self.engine) as session:
            sections = {
                s.section: s
                for s in session.scalars(
                    select(PolicySection).where(
                        PolicySection.index_id == index.index_id,
                        PolicySection.section.in_(section_numbers),
                    )
                )
            }
        passages: list[PolicyPassage] = []
        covered_sections: set[str] = set()
        covered_nodes: set[str] = set()
        remaining = self.settings.rag_context_tokens
        for candidate in candidates:
            node = candidate.node
            section_number = node.metadata["section"]
            if section_number in covered_sections or node.node_id in covered_nodes:
                continue
            section = sections[section_number]
            base = {
                "source": index.source,
                "document_version": index.document_version,
                "section": section.section,
                "title": " > ".join(section.hierarchy),
                "page_start": section.page_start,
                "page_end": section.page_end,
            }
            expanded = PolicyPassage(text=section.text, **base)
            original = PolicyPassage(text=node.metadata["body"], **base)
            expanded_cost = token_count(expanded.model_dump_json())
            if expanded_cost <= remaining:
                chosen, cost = expanded, expanded_cost
                covered_sections.add(section_number)
            else:
                chosen, cost = original, token_count(original.model_dump_json())
                if cost > remaining:
                    continue
            passages.append(chosen)
            covered_nodes.add(node.node_id)
            remaining -= cost
            if len(passages) == limit:
                break
        return passages
