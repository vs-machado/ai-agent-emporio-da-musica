"""Seleção do avaliador sem banco, inferência ou execução real de benchmark."""

import sys
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

import app.benchmarks.__main__ as cli


@pytest.fixture
def settings(monkeypatch):
    settings = SimpleNamespace(
        openrouter_api_key=SecretStr("openrouter-test-key"),
        google_api_key=SecretStr("google-test-key"),
        chat_primary_model="openrouter/deepseek/deepseek-v4.1-flash",
        rag_embedding_model="gemini-embedding-001",
    )
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    return settings


@pytest.mark.parametrize(
    ("args", "model", "provider", "base_url", "key"),
    [
        (
            [],
            "xiaomi/mimo-v2.6-flash",
            "openrouter",
            "https://openrouter.ai/api/v1",
            "openrouter-test-key",
        ),
        (
            ["--judge-provider", "google"],
            "gemini-2.5-flash",
            "google",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            "google-test-key",
        ),
        (
            ["--judge-model", "xiaomi/mimo-v2.6-pro"],
            "xiaomi/mimo-v2.6-pro",
            "openrouter",
            "https://openrouter.ai/api/v1",
            "openrouter-test-key",
        ),
    ],
)
def test_cli_routes_judge_and_preserves_google_embeddings(
    monkeypatch, tmp_path, settings, args, model, provider, base_url, key
):
    import openai
    import ragas.llms

    import app.rag.embeddings as embeddings

    observed = {}

    class Client:
        def __init__(self, **kwargs):
            observed["client"] = kwargs
            self.http_client = kwargs["http_client"]

        async def close(self):
            await self.http_client.aclose()
            observed["closed"] = True

    def llm_factory(name, **kwargs):
        observed["model"] = name
        return object()

    def build_embeddings(config):
        observed["embedding_key"] = config.google_api_key.get_secret_value()
        return object()

    async def run_benchmark(*args, **kwargs):
        observed["judge"] = kwargs["judge_config"]
        observed["usage_recorder"] = kwargs["judge_usage"]
        return tmp_path, {"status": "completed"}

    monkeypatch.setattr(sys, "argv", ["benchmark", "--mode", "live", *args])
    monkeypatch.setattr(openai, "AsyncOpenAI", Client)
    monkeypatch.setattr(ragas.llms, "llm_factory", llm_factory)
    monkeypatch.setattr(embeddings, "build_embeddings", build_embeddings)
    monkeypatch.setattr(cli, "EvaluationEmbeddings", lambda value: value)
    monkeypatch.setattr(cli, "RagasScorer", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "run_benchmark", run_benchmark)
    monkeypatch.setattr(
        cli, "create_database_engine", lambda config: SimpleNamespace(dispose=lambda: None)
    )

    assert cli.main() == 0
    assert observed["model"] == model
    assert observed["client"]["api_key"] == key
    assert observed["client"]["base_url"] == base_url
    assert observed["client"]["max_retries"] == 0
    assert observed["judge"]["model"] == model
    assert observed["judge"]["provider"] == provider
    assert observed["judge"]["base_url"] == base_url
    assert observed["judge"]["embedding_provider"] == "google"
    assert observed["embedding_key"] == "google-test-key"
    assert observed["usage_recorder"] is not None
    assert observed["closed"]


@pytest.mark.parametrize(
    ("missing_key", "message"),
    [
        ("openrouter_api_key", "OPENROUTER_API_KEY necessária para o avaliador"),
        ("google_api_key", "GOOGLE_API_KEY necessária para os embeddings"),
    ],
)
def test_cli_explains_missing_judge_or_embedding_key(
    monkeypatch, settings, capsys, missing_key, message
):
    setattr(settings, missing_key, None)
    monkeypatch.setattr(sys, "argv", ["benchmark"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert message in capsys.readouterr().err
