"""Integração LlamaIndex/pgvector com embeddings locais determinísticos."""

import unicodedata
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.schema import NodeWithScore, TextNode
from pydantic import PrivateAttr, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import PolicyIndex, PolicySection, PolicyVector
from app.rag.errors import PolicyIndexingError
from app.rag.evaluation import evaluate
from app.rag.indexing import DEFAULT_MANUAL_PATH, index_manual
from app.rag.parser import token_count
from app.rag.retrieval import LlamaIndexPolicyRetriever
from app.schemas.policies import PolicyPassage, PolicySearch
from app.services.errors import PolicyRetrievalError
from app.tools import build_tools


class LocalEmbedding(BaseEmbedding):
    """Embeddings sintéticos: verificam integração, não qualidade semântica do Gemini."""

    _calls: int = PrivateAttr(default=0)

    def _get_text_embedding(self, text):
        self._calls += 1
        text = "".join(
            c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c)
        )
        terms = ["pix", "garantia", "jadlog", "privacidade", "parcela"]
        return [0.1, *[float(term in text) for term in terms], *([0.0] * 762)]

    def _get_query_embedding(self, query):
        return self._get_text_embedding(query)

    async def _aget_query_embedding(self, query):
        return self._get_query_embedding(query)


class FailingEmbedding(LocalEmbedding):
    def _get_text_embedding(self, text):
        raise TimeoutError("private-provider-secret")


@pytest.fixture
def rag_settings(engine):
    url = engine.url
    return Settings(
        _env_file=None,
        postgres_db=url.database,
        postgres_user=url.username,
        postgres_password=url.password,
        postgres_host=url.host,
        postgres_port=url.port,
        google_api_key=None,
    )


def test_indexing_is_idempotent_and_persists_llamaindex_nodes(engine, rag_settings):
    embeddings = LocalEmbedding()
    first = index_manual(engine, rag_settings, embed_model=embeddings)
    calls = embeddings._calls
    second = index_manual(engine, rag_settings, embed_model=embeddings)
    assert first.status == "indexed"
    assert second.status == "unchanged"
    assert second.index_id == first.index_id
    assert embeddings._calls == calls
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PolicyIndex)) == 1
        assert session.scalar(select(func.count()).select_from(PolicyVector)) == 25
        assert session.scalar(select(func.count()).select_from(PolicySection)) == 28
        node = session.scalar(select(PolicyVector))
        assert len(node.embedding) == 768
        assert "_node_content" in node.metadata_


def test_vector_search_uses_pgvector_and_returns_complementary_sections(engine, rag_settings):
    embeddings = LocalEmbedding()
    index_manual(engine, rag_settings, embed_model=embeddings)
    retriever = LlamaIndexPolicyRetriever(engine, rag_settings, embeddings)
    result = retriever.retrieve("PIX", 5)
    assert result.mode == "vector"
    passages = result.passages
    assert passages[0].section in {"3", "6.2"}
    assert {"3", "6.2"} <= {p.section for p in passages}
    assert len(passages) == 5
    assert all(p.document_version == "2.1" for p in passages)
    assert sum(token_count(p.model_dump_json()) for p in passages) <= 2000


def test_embedding_failure_uses_textual_fallback_and_tools_keep_page_range(engine, rag_settings):
    index_manual(engine, rag_settings, embed_model=LocalEmbedding())
    retriever = LlamaIndexPolicyRetriever(engine, rag_settings, FailingEmbedding())
    tool = next(
        t for t in build_tools(sessionmaker(engine), retriever) if t.name == "search_policy"
    )
    result = tool.invoke({"query": "Jadlog"})
    assert result["status"] == "ok"
    passage = next(p for p in result["passages"] if p["section"] == "5.2")
    assert (passage["page_start"], passage["page_end"]) == (4, 5)
    assert "cotação individual" in passage["text"]
    assert "private-provider-secret" not in str(result)


def test_missing_api_key_still_allows_textual_search(engine, rag_settings):
    index_manual(engine, rag_settings, embed_model=LocalEmbedding())
    retriever = LlamaIndexPolicyRetriever(engine, rag_settings)
    assert "8.1" in {p.section for p in retriever.search("garantia legal")}
    assert retriever.search("xyznonexistentword") == []


def test_missing_active_index_is_unavailable_not_empty_search(engine, rag_settings):
    retriever = LlamaIndexPolicyRetriever(engine, rag_settings, LocalEmbedding())
    with pytest.raises(PolicyRetrievalError):
        retriever.search("garantia")
    tool = next(
        t for t in build_tools(sessionmaker(engine), retriever) if t.name == "search_policy"
    )
    assert tool.invoke({"query": "garantia"})["status"] == "unavailable"


def test_failed_update_preserves_active_version_and_all_rows(engine, rag_settings):
    first = index_manual(engine, rag_settings, embed_model=LocalEmbedding())
    changed = rag_settings.model_copy(update={"rag_chunk_tokens": 900})
    with pytest.raises(PolicyIndexingError):
        index_manual(engine, changed, embed_model=FailingEmbedding())
    with Session(engine) as session:
        assert (
            session.scalar(select(PolicyIndex.index_id).where(PolicyIndex.is_active))
            == first.index_id
        )
        assert session.scalar(select(func.count()).select_from(PolicyIndex)) == 1
        assert session.scalar(select(func.count()).select_from(PolicyVector)) == first.chunk_count


def test_changed_config_activates_new_version_and_reactivation_needs_no_embeddings(
    engine, rag_settings
):
    first = index_manual(engine, rag_settings, embed_model=LocalEmbedding())
    changed = rag_settings.model_copy(update={"rag_chunk_tokens": 900})
    second = index_manual(engine, changed, embed_model=LocalEmbedding())
    assert first.index_id != second.index_id
    with Session(engine) as session:
        assert (
            session.scalar(select(PolicyIndex.index_id).where(PolicyIndex.is_active))
            == second.index_id
        )
    previous = index_manual(engine, rag_settings, embed_model=FailingEmbedding())
    assert previous.status == "reactivated"
    assert previous.index_id == first.index_id
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(PolicyIndex).where(PolicyIndex.is_active)
            )
            == 1
        )


def test_mismatched_embedding_model_falls_back_without_mixing_versions(engine, rag_settings):
    first = index_manual(engine, rag_settings, embed_model=LocalEmbedding())
    changed = rag_settings.model_copy(update={"rag_embedding_model": "future-model"})
    second = index_manual(engine, changed, embed_model=LocalEmbedding())
    retriever = LlamaIndexPolicyRetriever(engine, rag_settings, FailingEmbedding())
    assert retriever.search("garantia legal")
    with Session(engine) as session, session.begin():
        session.query(PolicyVector).filter(
            PolicyVector.metadata_["index_id"].astext == str(first.index_id)
        ).update({PolicyVector.text: "exclusiveoldword"}, synchronize_session=False)
    assert retriever.search("exclusiveoldword") == []
    assert second.index_id != first.index_id


def test_bad_embedding_dimensions_do_not_activate_an_index(engine, rag_settings):
    from llama_index.core.embeddings import MockEmbedding

    with pytest.raises(PolicyIndexingError, match="inválidos"):
        index_manual(engine, rag_settings, embed_model=MockEmbedding(embed_dim=5))
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PolicyIndex)) == 0


def test_expansion_deduplicates_candidates_from_same_section(engine, rag_settings):
    index_manual(engine, rag_settings, embed_model=LocalEmbedding())
    retriever = LlamaIndexPolicyRetriever(engine, rag_settings, LocalEmbedding())
    with Session(engine) as session:
        index = session.scalar(select(PolicyIndex).where(PolicyIndex.is_active))
    candidates = [
        NodeWithScore(
            node=TextNode(
                id_=f"child-{i}", text="Jadlog", metadata={"section": "5.2", "body": "Jadlog"}
            ),
            score=1.0,
        )
        for i in range(2)
    ]
    passages = retriever._select_evidence(candidates, index, 5)
    assert len(passages) == 1
    assert "cotação individual" in passages[0].text


def test_budget_preserves_complete_original_when_expansion_cannot_fit(engine, rag_settings):
    index_manual(engine, rag_settings, embed_model=LocalEmbedding())
    retriever = LlamaIndexPolicyRetriever(engine, rag_settings, LocalEmbedding())
    with Session(engine, expire_on_commit=False) as session, session.begin():
        index = session.scalar(select(PolicyIndex).where(PolicyIndex.is_active))
        section = session.scalar(select(PolicySection).where(PolicySection.section == "5.2"))
        section.text = "contexto extenso " * 3000
    original = "Regra completa: frete especial exige cotação individual."
    candidate = NodeWithScore(
        node=TextNode(id_="child", text=original, metadata={"section": "5.2", "body": original}),
        score=1.0,
    )
    passages = retriever._select_evidence([candidate], index, 5)
    assert passages[0].text == original


def test_policy_contracts_use_five_and_validate_page_ranges():
    assert PolicySearch(query="garantia").limit == 5
    with pytest.raises(ValidationError):
        PolicyPassage(
            text="Regra",
            source=DEFAULT_MANUAL_PATH.name,
            page_start=5,
            page_end=4,
            section="5.2",
            title="Frete",
            document_version="2.1",
        )


def test_persistence_failure_rolls_back_new_version_and_activation(
    engine, rag_settings, monkeypatch
):
    import app.rag.indexing as indexing

    first = index_manual(engine, rag_settings, embed_model=LocalEmbedding())
    original_activate = indexing.activate

    def fail_after_activation(session, index_id):
        original_activate(session, index_id)
        raise RuntimeError("Simulação de falha antes do commit")

    monkeypatch.setattr(indexing, "activate", fail_after_activation)
    changed = rag_settings.model_copy(update={"rag_chunk_tokens": 900})
    with pytest.raises(RuntimeError):
        index_manual(engine, changed, embed_model=LocalEmbedding())
    with Session(engine) as session:
        assert (
            session.scalar(select(PolicyIndex.index_id).where(PolicyIndex.is_active))
            == first.index_id
        )
        assert session.scalar(select(func.count()).select_from(PolicyIndex)) == 1
        assert session.scalar(select(func.count()).select_from(PolicyVector)) == first.chunk_count


def test_concurrent_identical_indexing_does_not_duplicate_rows(engine, rag_settings):
    barrier = Barrier(2)

    class CoordinatedEmbedding(LocalEmbedding):
        def _get_text_embedding(self, text):
            if self._calls == 0:
                barrier.wait(timeout=10)
            return super()._get_text_embedding(text)

    def run():
        return index_manual(engine, rag_settings, embed_model=CoordinatedEmbedding())

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run)
        second = executor.submit(run)
        results = [first.result(), second.result()]
    assert {r.status for r in results} == {"indexed", "unchanged"}
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PolicyIndex)) == 1
        assert session.scalar(select(func.count()).select_from(PolicyVector)) == 25


def test_evaluation_reports_actual_fallback_and_missing_evidence(engine, rag_settings):
    index_manual(engine, rag_settings, embed_model=LocalEmbedding())
    report = evaluate(LlamaIndexPolicyRetriever(engine, rag_settings, FailingEmbedding()))
    assert report["case_count"] == 10
    assert all(r["mode"] == "text_fallback" for r in report["results"])
    assert all(r["context_tokens"] <= 2000 for r in report["results"])
    assert all(r["complete"] == (not r["missing_sections"]) for r in report["results"])
