"""Modelos LangChain sobre LiteLLM, com configuração explícita por provedor."""

import json
from copy import deepcopy
from dataclasses import dataclass

import litellm
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_litellm import ChatLiteLLM
from openai import OpenAIError
from pydantic import Field

from app.config import Settings


class ModelOutputError(ValueError):
    """O provedor retornou uma resposta incompatível com execução segura de tools."""


class ToolCallingLiteLLM(ChatLiteLLM):
    """Preserva metadados de chamadas ao reutilizar mensagens no mesmo modelo."""

    api_key: str | None = Field(default=None, exclude=True, repr=False)
    openrouter_api_key: str | None = Field(default=None, exclude=True, repr=False)

    def _create_chat_result(self, response):
        """Valida argumentos antes da conversão permissiva do adaptador upstream.

        Args:
            response: Resposta de completion normalizada pelo LiteLLM.

        Returns:
            Resultado LangChain com mensagens e metadados do provedor preservados.

        Raises:
            ModelOutputError: Resposta ausente, JSON inválido ou argumentos não estruturados.
        """
        try:
            if not response.get("choices"):
                raise ModelOutputError("Resposta sem mensagem do modelo")
            for choice in response["choices"]:
                for call in choice["message"].get("tool_calls") or []:
                    arguments = call["function"].get("arguments", "{}")
                    parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
                    if not isinstance(parsed, dict):
                        raise ModelOutputError("Argumentos de tool devem ser um objeto")
            return super()._create_chat_result(response)
        except (ValueError, TypeError, KeyError) as exc:
            raise ModelOutputError("Resposta incompatível com o contrato do modelo") from exc

    def _create_message_dicts(self, messages, stop):
        """Reanexa extensões de tool calls apenas ao reenviar ao mesmo modelo.

        Args:
            messages: Histórico interno completo, inclusive chamadas e resultados de tools.
            stop: Sequências de parada encaminhadas pelo LangChain.

        Returns:
            Mensagens LiteLLM e parâmetros, mantendo extensões como assinaturas Gemini
            sem misturá-las ao contexto enviado ao outro provedor.
        """
        converted, params = super()._create_message_dicts(messages, stop)
        for original, payload in zip(messages, converted, strict=True):
            if not isinstance(original, AIMessage):
                continue
            if original.response_metadata.get("model_name") != self.model:
                continue
            raw_calls = [
                call.model_dump(exclude_none=True) if hasattr(call, "model_dump") else call
                for call in original.additional_kwargs.get("tool_calls") or []
            ]
            raw_by_id = {call["id"]: call for call in raw_calls}
            for call in payload.get("tool_calls", []):
                raw = raw_by_id.get(call["id"], {})
                for key, value in raw.items():
                    if key not in {"id", "type", "function"}:
                        call[key] = deepcopy(value)
            if "provider_specific_fields" in original.additional_kwargs:
                payload["provider_specific_fields"] = deepcopy(
                    original.additional_kwargs["provider_specific_fields"]
                )
        return converted, params


@dataclass(frozen=True)
class AgentModels:
    primary: BaseChatModel
    primary_name: str
    fallback: BaseChatModel | None = None
    fallback_name: str | None = None


def is_recoverable(error: Exception) -> bool:
    if isinstance(error, (litellm.Timeout, litellm.APIConnectionError, litellm.RateLimitError)):
        return True
    status = getattr(error, "status_code", None)
    return isinstance(error, OpenAIError) and isinstance(status, int) and 500 <= status < 600


def build_models(settings: Settings) -> AgentModels | None:
    """Constrói adaptadores sem inferência e sem configurar fallback sem sua chave.

    Args:
        settings: Chaves, identificadores dos modelos e limites por chamada.

    Returns:
        Modelos com retries e cache LangChain desabilitados, ou None sem chave primária.
        A construção não valida disponibilidade remota nem exporta chaves ao ambiente.
    """
    if settings.google_api_key is None:
        return None
    common = {
        "request_timeout": settings.chat_model_timeout,
        "max_tokens": settings.chat_max_output_tokens,
        "temperature": 0,
        "max_retries": 0,
        "streaming": False,
        "cache": False,
        "model_kwargs": {"num_retries": 0},
    }
    primary = ToolCallingLiteLLM(
        model=settings.chat_primary_model,
        api_key=settings.google_api_key.get_secret_value(),
        **common,
    )
    fallback = None
    if settings.openrouter_api_key is not None:
        fallback = ToolCallingLiteLLM(
            model=settings.chat_fallback_model,
            api_key=settings.openrouter_api_key.get_secret_value(),
            **common,
        )
    return AgentModels(
        primary,
        settings.chat_primary_model,
        fallback,
        settings.chat_fallback_model if fallback else None,
    )
