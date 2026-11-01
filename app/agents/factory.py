"""Composição do ChatService sem chamadas a provedores na inicialização."""

from collections.abc import Sequence

from langchain_core.tools import BaseTool

from app.agents.models import AgentModels, build_models
from app.agents.runner import LangChainChatAgent
from app.config import Settings
from app.services.chat import ChatService


def build_chat_service(
    settings: Settings, tools: Sequence[BaseTool], models: AgentModels | None = None
) -> ChatService:
    configured = models or build_models(settings)
    agent = LangChainChatAgent(configured, tools, settings) if configured else None
    return ChatService(agent, max_history_messages=settings.chat_history_max_messages)
