"""Configurações da aplicação carregadas do ambiente."""

from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    google_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    chat_primary_model: str = "gemini/gemini-2.5-flash-lite"
    chat_fallback_model: str = "openrouter/deepseek/deepseek-v4.1-flash"
    chat_model_timeout: float = Field(default=20, gt=0, le=120)
    chat_max_output_tokens: int = Field(default=2048, ge=128, le=8192)
    chat_max_model_calls: int = Field(default=6, ge=1, le=20)
    chat_max_tool_calls: int = Field(default=12, ge=1, le=50)
    chat_history_max_messages: int = Field(default=100, ge=2, le=1000)
    rag_embedding_model: str = "gemini-embedding-001"
    rag_embedding_dimensions: Literal[768] = 768
    rag_top_k: int = Field(default=5, ge=1, le=30)
    rag_context_tokens: int = Field(default=2000, ge=512, le=16000)
    rag_chunk_tokens: int = Field(default=1000, ge=256, le=8000)

    @field_validator("rag_embedding_dimensions", mode="before")
    @classmethod
    def normalize_embedding_dimensions(cls, value):
        # Variáveis de ambiente são strings; o schema continua fixado em 768.
        return 768 if value == "768" else value

    @field_validator("google_api_key", "openrouter_api_key", mode="before")
    @classmethod
    def normalize_optional_key(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def validate_rag_budget(self):
        if self.rag_chunk_tokens + 256 > self.rag_context_tokens:
            raise ValueError("O orçamento RAG deve comportar um chunk e suas referências")
        return self

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
