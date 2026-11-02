"""Configuração de embeddings recebida pelo ambiente do container."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_default_chat_uses_deepseek_without_fallback(monkeypatch):
    monkeypatch.delenv("CHAT_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("CHAT_FALLBACK_MODEL", raising=False)
    settings = Settings(
        _env_file=None,
        postgres_db="emporio_test",
        postgres_user="test",
        postgres_password="test",
    )
    assert settings.chat_primary_model == "openrouter/deepseek/deepseek-v4.1-flash"
    assert settings.chat_fallback_model is None


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
