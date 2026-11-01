"""Fachada de atendimento para API e interface, com agente injetável."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    messages_from_dict,
    messages_to_dict,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.models.conversation import Conversation, ConversationTurn
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
    def __init__(
        self,
        agent: ChatAgent | None = None,
        max_history_messages: int = 100,
        session_factory: sessionmaker[Session] | None = None,
    ):
        self.agent = agent
        self.max_history_messages = max_history_messages
        self.session_factory = session_factory

    def chat(self, request: ChatRequest, history: Sequence[BaseMessage] = ()) -> ChatTurn:
        """Carrega a conversa e grava atomicamente as mensagens completas do turno.

        Args:
            request: Texto, UUID da conversa e ID estável da solicitação.
            history: Histórico interno para uso sem persistência configurada.

        Returns:
            Resposta pública e mensagens apenas deste turno.
            Sem modelo configurado, retorna indisponibilidade sem acessar provedores.

        Raises:
            ValueError: Histórico inválido ou além do limite; não há truncamento automático.
        """
        if self.session_factory is None:
            return self._run(request, history)
        if history:
            raise ValueError("Com persistência, o histórico é carregado pelo UUID da conversa")
        with self.session_factory.begin() as session:
            # O insert concorrente aguarda a criação da conversa; o lock dura todo o turno.
            session.execute(
                insert(Conversation)
                .values(conversation_id=request.conversation_id)
                .on_conflict_do_nothing(index_elements=["conversation_id"])
            )
            session.execute(
                select(Conversation)
                .where(Conversation.conversation_id == request.conversation_id)
                .with_for_update()
            ).scalar_one()
            turns = session.scalars(
                select(ConversationTurn)
                .where(ConversationTurn.conversation_id == request.conversation_id)
                .order_by(ConversationTurn.position)
            ).all()
            for saved in turns:
                if saved.request_id == request.request_id:
                    if saved.message != request.message:
                        raise ValueError(
                            "request_id já utilizado com outra mensagem nesta conversa"
                        )
                    return ChatTurn(
                        ChatResponse.model_validate(saved.response),
                        messages_from_dict(saved.messages),
                    )
            history = messages_from_dict([message for turn in turns for message in turn.messages])
            turn = self._run(request, history)
            validate_history(turn.messages, len(turn.messages))
            session.add(
                ConversationTurn(
                    conversation_id=request.conversation_id,
                    request_id=request.request_id,
                    position=len(turns),
                    message=request.message,
                    messages=messages_to_dict(turn.messages),
                    response=turn.response.model_dump(mode="json"),
                )
            )
            return turn

    def _run(self, request: ChatRequest, history: Sequence[BaseMessage]) -> ChatTurn:
        validate_history(history, self.max_history_messages)
        if self.agent is None:
            return ChatTurn(
                ChatResponse(
                    conversation_id=request.conversation_id,
                    request_id=request.request_id,
                    status="unavailable",
                    message=UNAVAILABLE_MESSAGE,
                ),
                [HumanMessage(content=request.message), AIMessage(content=UNAVAILABLE_MESSAGE)],
            )
        return self.agent.run(request, history)
