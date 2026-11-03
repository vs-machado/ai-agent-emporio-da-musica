"""BM25, fusão e degradação, incluindo normalização real no PostgreSQL."""

from math import log1p

import pytest
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.schema import NodeWithScore, TextNode
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PolicyIndex, PolicyVector
from app.rag.bm25 import bm25_scores
from app.rag.indexing import index_manual
from app.rag.parser import token_count
from app.rag.retrieval import (
    HybridPolicyRetriever,
    LlamaIndexPolicyRetriever,
    build_policy_retriever,
)
from app.services.errors import PolicyRetrievalError


@pytest.fixture
def settings():
    return Settings(
        _env_file=None, postgres_db="unused", postgres_user="unused", postgres_password="unused"
    )


def candidate(node_id, score=1.0):
    return NodeWithScore(node=TextNode(id_=node_id, text=node_id), score=score)


def test_bm25_matches_okapi_formula_and_uses_full_corpus():
    # N=3, df(pix)=1, avgdl=2, dl=2, tf=2.
    scores = bm25_scores([["pix", "pix"], ["loja", "loja"], ["loja", "loja"]], ["pix"])
    assert scores == pytest.approx([log1p(2.5 / 1.5) * 2 * 2.2 / (2 + 1.2), 0, 0])


def test_bm25_rewards_rare_terms_and_normalizes_length():
    docs = [["pix"], ["loja"], ["loja"], ["pix", *(["loja"] * 20)]]
    pix = bm25_scores(docs, ["pix"])
    assert pix[0] > pix[3] > 0
    assert (
        bm25_scores([["raro", "comum"], ["comum"]], ["raro"])[0]
        > bm25_scores([["raro", "comum"], ["comum"]], ["comum"])[0]
    )
    assert bm25_scores(docs, ["pix", "pix"]) == pix


def test_bm25_empty_and_unknown_terms_do_not_return_matches():
    assert bm25_scores([], ["pix"]) == []
    assert bm25_scores([[], []], ["pix"]) == [0, 0]
    assert bm25_scores([["pix"], []], ["unknown"]) == [0, 0]
    assert bm25_scores([["pix"]], []) == [0]


def test_factory_defaults_to_hybrid_and_keeps_legacy_strategy(settings):
    from sqlalchemy import create_engine

    engine = create_engine("postgresql+psycopg://unused:unused@localhost/unused")
    assert isinstance(build_policy_retriever(engine, settings), HybridPolicyRetriever)
    legacy = settings.model_copy(update={"rag_strategy": "vector_with_text_fallback"})
    assert type(build_policy_retriever(engine, legacy)) is LlamaIndexPolicyRetriever


def test_rrf_uses_ranks_not_raw_scores_and_deduplicates(settings):
    retriever = HybridPolicyRetriever.__new__(HybridPolicyRetriever)
    retriever.settings = settings
    fused, diagnostics = retriever._fuse(
        {
            "vector": [candidate("a", 999), candidate("shared", 0.01), candidate("shared")],
            "bm25": [candidate("b", 0.01), candidate("shared", 999)],
        }
    )
    assert [item.node_id for item in fused] == ["shared", "a", "b"]
    assert fused[0].score == pytest.approx(2 / 62)
    assert diagnostics[0]["ranks"] == {"vector": 2, "bm25": 2}


def test_hybrid_metadata_reports_effective_configuration(settings):
    retriever = HybridPolicyRetriever.__new__(HybridPolicyRetriever)
    retriever.settings = settings.model_copy(update={"rag_bm25_top_k": 7, "rag_rrf_k": 40})
    metadata = retriever.metadata()
    assert metadata["strategy"] == "hybrid"
    assert metadata["parameters"]["bm25"]["top_k"] == 7
    assert metadata["parameters"]["fusion"]["k"] == 40
    assert "text" not in metadata["parameters"]


def test_hybrid_requires_active_index(engine, settings):
    retriever = HybridPolicyRetriever(engine, settings, MockEmbedding(embed_dim=768))
    with pytest.raises(PolicyRetrievalError, match="Nenhuma versão"):
        retriever.retrieve("PIX")
    with pytest.raises(ValueError, match="limite"):
        retriever.retrieve("PIX", 6)


@pytest.fixture
def indexed(engine, settings):
    embeddings = MockEmbedding(embed_dim=768)
    index_manual(engine, settings, embed_model=embeddings)
    return HybridPolicyRetriever(engine, settings, embeddings)


def test_postgres_bm25_handles_long_queries_stemming_and_inactive_versions(
    engine, settings, indexed
):
    with Session(engine) as session:
        first = session.scalar(select(PolicyIndex).where(PolicyIndex.is_active))
    assert indexed._bm25_search("xyznonexistentword", first) == []
    assert indexed._bm25_search("devoluções", first)
    hits = indexed._bm25_search("Produto em promoção tem desconto adicional no PIX?", first)
    assert {"3", "6.2"} <= {hit.metadata["section"] for hit in hits}
    changed = settings.model_copy(update={"rag_chunk_tokens": 900})
    second = index_manual(engine, changed, embed_model=MockEmbedding(embed_dim=768))
    with Session(engine) as session, session.begin():
        session.execute(
            update(PolicyVector)
            .where(PolicyVector.metadata_["index_id"].astext == str(first.index_id))
            .values(text="exclusiveoldword")
        )
    result = indexed.retrieve("exclusiveoldword")
    assert result.diagnostics["index_id"] == str(second.index_id)
    assert result.diagnostics["candidate_counts"]["bm25"] == 0


def test_hybrid_returns_fused_bounded_evidence(indexed):
    result = indexed.retrieve("PIX promoção")
    assert result.mode == "hybrid"
    assert result.diagnostics["candidate_counts"]["vector"] > 0
    assert result.diagnostics["candidate_counts"]["bm25"] > 0
    assert result.diagnostics["failed_branches"] == []
    assert {"3", "6.2"} <= {passage.section for passage in result.passages}
    assert len(result.passages) <= 5
    assert sum(token_count(p.model_dump_json()) for p in result.passages) <= 2000


@pytest.mark.parametrize("failed,surviving", [("vector", "bm25"), ("bm25", "vector")])
def test_single_branch_failure_is_reported_without_provider_secrets(
    indexed, monkeypatch, failed, surviving
):
    def fail(*args):
        raise TimeoutError("private-provider-secret")

    monkeypatch.setattr(indexed, f"_{failed}_search", fail)
    result = indexed.retrieve("PIX")
    assert result.mode == f"{surviving}_fallback"
    assert result.passages
    assert result.diagnostics["failed_branches"] == [failed]
    assert "private-provider-secret" not in str(result)


def test_both_failures_raise_unavailable(indexed, monkeypatch):
    def fail(*args):
        raise TimeoutError("private-provider-secret")

    monkeypatch.setattr(indexed, "_vector_search", fail)
    monkeypatch.setattr(indexed, "_bm25_search", fail)
    with pytest.raises(PolicyRetrievalError, match="Busca vetorial e BM25 indisponíveis"):
        indexed.retrieve("PIX")


def test_empty_vector_still_runs_bm25(indexed, monkeypatch):
    monkeypatch.setattr(indexed, "_vector_search", lambda *args: [])
    result = indexed.retrieve("PIX")
    assert result.mode == "hybrid"
    assert result.passages
    assert result.diagnostics["candidate_counts"]["vector"] == 0
