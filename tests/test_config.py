"""Configuração de embeddings recebida pelo ambiente do container."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_default_chat_uses_deepseek_without_fallback(monkeypatch):
    monkeypatch.delenv("CHAT_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("CHAT_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("CHAT_MODEL_RETRIES", raising=False)
    settings = Settings(
        _env_file=None,
        postgres_db="emporio_test",
        postgres_user="test",
        postgres_password="test",
    )
    assert settings.chat_primary_model == "openrouter/deepseek/deepseek-v4.1-flash"
    assert settings.chat_fallback_model is None
    assert settings.chat_model_retries == 2


@pytest.mark.parametrize("value", ["0", "1", "2", "3"])
def test_model_retry_budget_is_loaded_from_environment(monkeypatch, value):
    monkeypatch.setenv("CHAT_MODEL_RETRIES", value)
    settings = Settings(
        _env_file=None, postgres_db="emporio_test", postgres_user="test", postgres_password="test"
    )
    assert settings.chat_model_retries == int(value)


@pytest.mark.parametrize("value", ["-1", "4", "invalid"])
def test_invalid_model_retry_budget_is_rejected(monkeypatch, value):
    monkeypatch.setenv("CHAT_MODEL_RETRIES", value)
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            postgres_db="emporio_test",
            postgres_user="test",
            postgres_password="test",
        )


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_fallback_disables_model(value):
    settings = Settings(
        _env_file=None,
        postgres_db="emporio_test",
        postgres_user="test",
        postgres_password="test",
        chat_fallback_model=value,
    )
    assert settings.chat_fallback_model is None


def test_embedding_dimension_is_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_DIMENSIONS", "768")
    settings = Settings(
        _env_file=None, postgres_db="emporio_test", postgres_user="test", postgres_password="test"
    )
    assert settings.rag_embedding_dimensions == 768


@pytest.mark.parametrize("value", ["3072", "0", "invalid"])
def test_environment_cannot_change_fixed_embedding_dimension(monkeypatch, value):
    monkeypatch.setenv("RAG_EMBEDDING_DIMENSIONS", value)
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            postgres_db="emporio_test",
            postgres_user="test",
            postgres_password="test",
        )
