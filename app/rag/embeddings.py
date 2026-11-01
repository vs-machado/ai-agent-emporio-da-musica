"""Configuração explícita de embeddings, sem alterar Settings globais do LlamaIndex."""

from llama_index.embeddings.google_genai import GoogleGenAIEmbedding

from app.config import Settings
from app.rag.errors import PolicyIndexingError


def build_embeddings(settings: Settings) -> GoogleGenAIEmbedding:
    if not settings.google_api_key or not settings.google_api_key.get_secret_value().strip():
        raise PolicyIndexingError("Defina GOOGLE_API_KEY para gerar embeddings do manual.")
    return GoogleGenAIEmbedding(
        model_name=settings.rag_embedding_model,
        api_key=settings.google_api_key.get_secret_value(),
        embedding_config={"output_dimensionality": settings.rag_embedding_dimensions},
        embed_batch_size=10,
        timeout=20,
        retries=2,
        retry_max_seconds=5,
    )
