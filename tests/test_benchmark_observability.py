"""Contratos de observabilidade com retrievers sintéticos e sem chamadas externas."""

import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest

from app.benchmarks.reporting import RunLog, snapshot
from app.benchmarks.runner import RecordingRetriever
from app.benchmarks.usage import JudgeUsageRecorder, run_token_usage
from app.config import Settings
from app.rag.retrieval import RetrievalResult


@pytest.mark.parametrize("strategy", ["vector_with_text_fallback", "hybrid", "future_strategy"])
def test_recording_preserves_opaque_strategy_parameters_and_diagnostics(strategy):
    metadata = {
        "strategy": strategy,
        "parameters": {"fusion": {"method": "rrf", "k": 60}, "reranker": None},
    }
    diagnostics = {"candidate_counts": {"vector": 8, "text": 10}, "fusion_seconds": 0.002}
    retriever = SimpleNamespace(
        metadata=lambda: metadata,
        retrieve=lambda *args: RetrievalResult([], strategy, 12, diagnostics),
    )
    recorder = RecordingRetriever(retriever)
    assert recorder.search("garantia", 3) == []
    call = recorder.calls[0]
    assert call["configuration"] == metadata
    assert call["mode"] == strategy
    assert call["diagnostics"] == diagnostics
    assert call["candidate_count"] == 12
    metadata["parameters"]["fusion"]["k"] = 99
    assert call["configuration"]["parameters"]["fusion"]["k"] == 60


def test_snapshot_uses_retriever_configuration_and_stable_hash(monkeypatch):
    import app.benchmarks.reporting as reporting

    monkeypatch.setattr(
        reporting, "Session", lambda *args: nullcontext(SimpleNamespace(scalar=lambda *args: None))
    )
    settings = Settings(
        _env_file=None, postgres_db="test", postgres_user="test", postgres_password="test"
    )
    retriever = SimpleNamespace(
        metadata=lambda: {"strategy": "hybrid", "parameters": {"text_k": 10, "vector_k": 8}}
    )
    first = snapshot(None, settings, retriever)["rag"]
    retriever.metadata = lambda: {"parameters": {"vector_k": 8, "text_k": 10}, "strategy": "hybrid"}
    reordered = snapshot(None, settings, retriever)["rag"]
    assert first["retrieval_sha256"] == reordered["retrieval_sha256"]
    assert first["retrieval"]["parameters"]["vector_k"] == 8
    retriever.metadata = lambda: {
        "strategy": "hybrid",
        "parameters": {"text_k": 10, "vector_k": 20},
    }
    assert (
        first["retrieval_sha256"] != snapshot(None, settings, retriever)["rag"]["retrieval_sha256"]
    )


def test_report_preserves_comparison_configuration_and_tokens_by_model(tmp_path):
    configuration = {"rag": {"retrieval": {"strategy": "hybrid", "parameters": {}}}}
    log = RunLog(tmp_path, {"configuration": configuration, "mode": "live"})
    results = [
        {
            "status": "ok",
            "model_calls": [
                {
                    "model": "primary",
                    "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                },
                {
                    "model": "fallback",
                    "usage": {"input_tokens": 80, "output_tokens": 10, "total_tokens": 90},
                },
            ],
            "judge_model_calls": [
                {
                    "model": "judge",
                    "usage": {"input_tokens": 300, "output_tokens": 40, "total_tokens": 340},
                },
            ],
        }
    ]
    report = log.finish(results, "completed")
    assert report["configuration"] == configuration
    assert report["token_usage"]["agent"]["total_tokens"] == 210
    assert report["token_usage"]["agent"]["by_model"]["fallback"]["total_tokens"] == 90
    assert report["token_usage"]["judge"]["total_tokens"] == 340


def test_missing_usage_is_not_reported_as_zero_and_skips_are_excluded():
    result = run_token_usage(
        [
            {
                "status": "ok",
                "model_calls": [{"model": "unknown", "usage": None}],
                "judge_model_calls": [],
            },
            {"status": "error"},
            {"status": "skipped"},
        ]
    )
    assert result["agent"]["total_tokens"] is None
    assert result["agent"]["cases_without_records"] == 1
    assert result["agent"]["status"] == "partial"
    assert result["embeddings"]["total_tokens"] is None
    assert result["cost"]["status"] == "not_calculated"
    disabled = run_token_usage([{"status": "ok", "model_calls": [], "judge_model_calls": []}])
    assert disabled["judge"]["status"] == "no_calls"


def test_judge_hook_counts_retries_and_keeps_missing_usage_unknown():
    recorder = JudgeUsageRecorder()

    async def capture():
        for raw in (None, {"prompt_tokens": 200, "completion_tokens": 30, "total_tokens": 230}):
            request = httpx.Request(
                "POST", "https://example.test/chat/completions", json={"model": "judge"}
            )
            await recorder.record_request(request)
            response = httpx.Response(
                200,
                request=request,
                json={"model": "judge", "usage": raw, "choices": [{"content": "not logged"}]},
            )
            await recorder.record_response(response)
        # Uma tentativa sem resposta deve continuar desconhecida, não virar zero.
        await recorder.record_request(
            httpx.Request("POST", "https://example.test/chat/completions", json={"model": "judge"})
        )

    asyncio.run(capture())
    summary = run_token_usage(
        [{"status": "ok", "model_calls": [], "judge_model_calls": recorder.calls}]
    )
    assert summary["judge"]["calls"] == 3
    assert summary["judge"]["calls_with_usage"] == 1
    assert summary["judge"]["total_tokens"] == 230
    assert summary["judge"]["status"] == "partial"
    assert "not logged" not in json.dumps(recorder.calls)
