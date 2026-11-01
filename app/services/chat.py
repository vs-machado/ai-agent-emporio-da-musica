"""Fachada de atendimento para API e interface, com agente injetável."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.schemas.chat import ChatRequest, ChatResponse

UNAVAILABLE_MESSAGE = (
    "Não consegui concluir o atendimento agora. Tente novamente em instantes "
    "ou entre em contato com a equipe da loja."
)
LIMIT_MESSAGE = (
    "Não consegui concluir a consulta dentro do limite deste atendimento. "
    "Podemos tentar uma pergunta mais específica ou consultar a equipe da loja."
)


@dataclass(frozen=True)
class ChatTurn:
    response: ChatResponse
    messages: list[BaseMessage]


class ChatAgent(Protocol):
    def run(self, request: ChatRequest, history: Sequence[BaseMessage]) -> ChatTurn:
        """Executa um turno e retorna suas mensagens completas, incluindo chamadas de tools."""
        ...


def validate_history(history: Sequence[BaseMessage], max_messages: int) -> None:
    """Rejeita histórico truncado ou com papéis externos ao diálogo.

    Args:
        history: Mensagens internas confiáveis, recuperadas pelo chamador.
        max_messages: Limite explícito, sem truncar pares de chamada e resultado.

    Raises:
        ValueError: Histórico excede o limite ou contém chamadas de tools sem resultado.
    """
    if len(history) > max_messages:
        raise ValueError("Histórico excede o limite de mensagens do atendimento")
    if history and not isinstance(history[0], HumanMessage):
        raise ValueError("O histórico deve começar por uma mensagem do cliente")
    pending: set[str] = set()
    for message in history:
        if isinstance(message, ToolMessage):
            if message.tool_call_id not in pending:
                raise ValueError("Resultado de tool sem chamada correspondente")
            pending.remove(message.tool_call_id)
        elif isinstance(message, AIMessage):
            if pending or message.invalid_tool_calls:
                raise ValueError("Histórico contém chamadas de tools incompletas")
            ids = [call["id"] for call in message.tool_calls]
            if not all(ids) or len(set(ids)) != len(ids):
                raise ValueError("IDs de chamadas de tools inválidos")
            pending.update(ids)
        elif not isinstance(message, HumanMessage) or pending:
            raise ValueError("Papel ou sequência inválida no histórico")
    if pending or (history and not isinstance(history[-1], AIMessage)):
        raise ValueError("O histórico deve terminar em um turno concluído")


class ChatService:
    def __init__(self, agent: ChatAgent | None = None, max_history_messages: int = 100):
        self.agent = agent
        self.max_history_messages = max_history_messages

    def chat(self, request: ChatRequest, history: Sequence[BaseMessage] = ()) -> ChatTurn:
        """Atende uma mensagem sem assumir persistência ou sessão HTTP.

        Args:
            request: Texto do cliente e ID estável da solicitação.
            history: Histórico interno completo; não aceitar papéis enviados pelo cliente.

        Returns:
            Resposta pública e mensagens apenas deste turno para persistência futura.
            Sem modelo configurado, retorna indisponibilidade sem acessar provedores.

        Raises:
            ValueError: Histórico inválido ou além do limite; não há truncamento automático.
        """
        validate_history(history, self.max_history_messages)
        if self.agent is None:
            return ChatTurn(
                ChatResponse(
                    request_id=request.request_id, status="unavailable", message=UNAVAILABLE_MESSAGE
                ),
                [HumanMessage(content=request.message), AIMessage(content=UNAVAILABLE_MESSAGE)],
            )
        return self.agent.run(request, history)
