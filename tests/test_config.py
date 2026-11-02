"""Configuração de embeddings recebida pelo ambiente do container."""

import pytest
from pydantic import ValidationError

from app.config import Settings


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
