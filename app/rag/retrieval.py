"""Retrieval vetorial + BM25/RRF, com expansão estrutural e baseline FTS."""

import logging
from dataclasses import dataclass, field
from typing import Any

from llama_index.core import VectorStoreIndex
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.core.vector_stores import MetadataFilter, MetadataFilters
from llama_index.vector_stores.postgres import PGVectorStore
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.models import PolicyIndex, PolicySection, PolicyVector
from app.rag.bm25 import K1, B, bm25_scores
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
    diagnostics: dict[str, Any] = field(default_factory=dict)


def build_policy_retriever(engine: Engine, settings: Settings):
    """Ponto único de seleção do retriever para atendimento, CLI e benchmark."""
    if settings.rag_strategy == "hybrid":
        return HybridPolicyRetriever(engine, settings)
    return LlamaIndexPolicyRetriever(engine, settings)


class LlamaIndexPolicyRetriever:
    def metadata(self) -> dict[str, Any]:
        """Contrato JSON público, sem credenciais, independente do avaliador.

        Estratégias futuras mantêm strategy/parameters e podem acrescentar parâmetros
        de fusão, reranking etc. Diagnósticos da consulta pertencem ao resultado.
        """
        return {
            "strategy": "vector_with_text_fallback",
            "version": 1,
            "parameters": {
                "vector": {
                    "metric": "cosine",
                    "top_k": self.settings.rag_top_k,
                    "embedding_model": self.settings.rag_embedding_model,
                    "embedding_dimensions": self.settings.rag_embedding_dimensions,
                },
                "text": {
                    "engine": "postgresql_fts",
                    "language": "portuguese",
                    "ranking": "ts_rank_cd",
                    "top_k": self.settings.rag_top_k,
                },
                "fallback_on": "vector_error",
                "context_tokens": self.settings.rag_context_tokens,
                "expansion": "full_subsection_if_budget_allows",
                "deduplication": "section_and_node",
                "max_passages": 5,
            },
        }

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
            self._select_evidence(candidates, index, limit),
            mode,
            len(candidates),
            {"candidate_counts": {mode: len(candidates)}},
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


class HybridPolicyRetriever(LlamaIndexPolicyRetriever):
    """Combina rankings vetorial e BM25 antes da expansão, sempre na mesma versão."""

    def metadata(self) -> dict[str, Any]:
        """Expõe parâmetros efetivos para comparação reproduzível nos benchmarks."""
        config = super().metadata()
        config.update(strategy="hybrid", version=1)
        parameters = config["parameters"]
        parameters.pop("text")
        parameters.pop("fallback_on")
        parameters.update(
            bm25={
                "engine": "in_process_bm25_okapi",
                "tokenizer": "postgresql_ts_debug_portuguese",
                "idf": "log1p((N-df+0.5)/(df+0.5))",
                "k1": K1,
                "b": B,
                "top_k": self.settings.rag_bm25_top_k,
                "corpus": "all_chunks_of_active_index",
            },
            fusion={"algorithm": "rrf", "k": self.settings.rag_rrf_k, "weights": [1, 1]},
            degradation="surviving_branch_on_error",
        )
        return config

    def _bm25_search(self, query: str, index: PolicyIndex) -> list[NodeWithScore]:
        """Lê corpus ativo e preserva frequências via ts_debug para BM25 em memória.

        Não reutiliza tsvector: posições podem ser truncadas e não representam uma
        contagem ilimitada de ocorrências. Estatísticas nunca incluem versões inativas.
        A leitura integral é deliberada para o manual pequeno; não mantém cache obsoleto.
        """
        statement = text(
            "SELECT node_id, text, metadata_, "
            "ARRAY(SELECT unnest(lexemes) FROM ts_debug('portuguese', text)) AS tokens "
            "FROM data_policy_vectors WHERE metadata_->>'index_id' = :index_id "
            "ORDER BY node_id"
        )
        with Session(self.engine) as session:
            rows = session.execute(statement, {"index_id": str(index.index_id)}).mappings().all()
            query_tokens = session.scalar(
                text("SELECT ARRAY(SELECT unnest(lexemes) FROM ts_debug('portuguese', :query))"),
                {"query": query},
            )
        scores = bm25_scores([row["tokens"] for row in rows], query_tokens)
        candidates = [
            NodeWithScore(
                node=TextNode(id_=row["node_id"], text=row["text"], metadata=row["metadata_"]),
                score=score,
            )
            for row, score in zip(rows, scores, strict=True)
            if score > 0
        ]
        return sorted(candidates, key=lambda item: (-item.score, item.node_id))[
            : self.settings.rag_bm25_top_k
        ]

    def _fuse(self, rankings: dict[str, list[NodeWithScore]]):
        scores: dict[str, float] = {}
        nodes: dict[str, TextNode] = {}
        ranks: dict[str, dict[str, int]] = {}
        for branch, candidates in rankings.items():
            seen = set()
            for rank, candidate in enumerate(candidates, start=1):
                node_id = candidate.node_id
                if node_id in seen:
                    continue
                seen.add(node_id)
                nodes[node_id] = candidate.node
                scores[node_id] = scores.get(node_id, 0.0) + 1 / (self.settings.rag_rrf_k + rank)
                ranks.setdefault(node_id, {})[branch] = rank
        ordered = sorted(scores, key=lambda node_id: (-scores[node_id], node_id))
        candidates = [
            NodeWithScore(node=nodes[node_id], score=scores[node_id]) for node_id in ordered
        ]
        diagnostics = [
            {"node_id": node_id, "ranks": ranks[node_id], "rrf_score": scores[node_id]}
            for node_id in ordered
        ]
        return candidates, diagnostics

    def retrieve(self, query: str, limit: int = 5) -> RetrievalResult:
        """Executa ambos os caminhos; falha de um degrada, falha dos dois indisponibiliza.

        Diagnósticos registram somente nomes dos caminhos com falha, sem mensagens
        de provedor ou credenciais. Resultado vazio de um caminho não é erro.
        """
        if not 1 <= limit <= 5:
            raise ValueError("O limite de evidências deve estar entre um e cinco")
        with Session(self.engine) as session:
            index = session.scalar(select(PolicyIndex).where(PolicyIndex.is_active))
            if index is None:
                raise PolicyRetrievalError("Nenhuma versão do manual está ativa.")
        rankings = {}
        failed = []
        for branch, search in (("vector", self._vector_search), ("bm25", self._bm25_search)):
            try:
                rankings[branch] = search(query, index)
            except Exception:
                logger.warning("Busca %s de políticas indisponível.", branch)
                failed.append(branch)
        if not rankings:
            raise PolicyRetrievalError("Busca vetorial e BM25 indisponíveis.")
        candidates, fusion = self._fuse(rankings)
        mode = "hybrid" if not failed else f"{next(iter(rankings))}_fallback"
        return RetrievalResult(
            self._select_evidence(candidates, index, limit),
            mode,
            len(candidates),
            {
                "index_id": str(index.index_id),
                "candidate_counts": {branch: len(items) for branch, items in rankings.items()},
                "failed_branches": failed,
                "fusion": fusion,
            },
        )
