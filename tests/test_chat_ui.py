"""Interface Gradio: sessão, retries e apresentação sem transcript interno."""

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import gradio as gr
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gradio.state_holder import SessionState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.models import ConversationTurn
from app.routes.chat import router
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat import ChatService, ChatTurn
from app.services.errors import ChatHistoryError, ChatHistoryLimitError, ChatRequestConflictError
from app.ui import ChatSession, ChatUI, ChatWorkspace, ConversationUI, build_chat_ui, mount_chat_ui


class RecordingAgent:
    def __init__(self, status="ok"):
        self.calls = []
        self.status = status

    def run(self, request, history):
        self.calls.append((request, list(history)))
        return ChatTurn(
            ChatResponse(
                conversation_id=request.conversation_id,
                request_id=request.request_id,
                status=self.status,
                message="Resposta pública",
            ),
            [
                HumanMessage(content=request.message),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "search_products", "args": {"query": "private"}, "id": "one"}
                    ],
                ),
                ToolMessage(content="private-tool-result", tool_call_id="one"),
                AIMessage(content="Resposta pública"),
            ],
        )


def test_ui_keeps_conversation_and_starts_new_one(engine):
    agent = RecordingAgent()
    service = ChatService(agent, session_factory=sessionmaker(engine))
    ui = ChatUI(lambda: service)
    session = ChatSession()
    list(ui.send("Olá", session))
    list(ui.send("Continue", session))
    assert agent.calls[0][0].conversation_id == agent.calls[1][0].conversation_id
    assert agent.calls[0][0].request_id != agent.calls[1][0].request_id
    assert [m.type for m in agent.calls[1][1]] == ["human", "ai", "tool", "ai"]
    assert [m["role"] for m in session.messages] == ["user", "assistant", "user", "assistant"]
    assert "private" not in json.dumps(session.messages)
    new = ui.new_conversation(session)[0]
    assert new.conversation_id != session.conversation_id
    assert not new.messages
    assert len(session.messages) == 4
    list(ui.send("Outra conversa", new))
    assert not agent.calls[-1][1]


def test_retry_recovers_saved_turn_after_response_failure_without_reexecuting_agent(engine):
    agent = RecordingAgent()
    service = ChatService(agent, session_factory=sessionmaker(engine))

    class LostResponse:
        def __init__(self):
            self.requests = []

        def chat(self, request):
            self.requests.append(request.model_copy())
            turn = service.chat(request)
            if len(self.requests) == 1:
                raise SQLAlchemyError("private-credentials")
            return turn

    transport = LostResponse()
    ui = ChatUI(lambda: transport)
    session = ChatSession()
    failed = list(ui.send("Busque violão", session))[-1]
    assert session.pending is not None
    assert len(session.messages) == 1
    assert "private" not in failed[6]
    assert failed[4]["interactive"]  # Retry disponível.
    assert not failed[3]["interactive"]  # Novo envio bloqueado até resolver a pendência.
    list(ui.send("Outra mensagem", session))
    assert len(transport.requests) == 1
    list(ui.retry(session))
    assert transport.requests[0] == transport.requests[1]
    assert len(agent.calls) == 1
    assert session.pending is None
    assert len(session.messages) == 2
    with Session(engine) as database:
        assert database.scalar(select(func.count()).select_from(ConversationTurn)) == 1


@pytest.mark.parametrize("status", ["ok", "unavailable", "limit_reached"])
def test_completed_turn_is_not_retryable(status):
    agent = RecordingAgent(status)
    ui = ChatUI(lambda: ChatService(agent))
    session = ChatSession()
    output = list(ui.send("Olá", session))[-1]
    assert session.pending is None
    assert not output[4]["interactive"]
    assert output[3]["interactive"]
    assert session.messages[-1] == {"role": "assistant", "content": "Resposta pública"}
    list(ui.retry(session))
    assert len(agent.calls) == 1


@pytest.mark.parametrize("text", ["", "   ", "x" * 4001])
def test_ui_validates_message_before_creating_turn(text):
    agent = RecordingAgent()
    ui = ChatUI(lambda: ChatService(agent))
    session = ChatSession()
    output = list(ui.send(text, session))[-1]
    assert "4.000" in output[6]
    assert session.pending is None
    assert not session.messages
    assert not agent.calls


@pytest.mark.parametrize(
    "error",
    [
        ChatHistoryLimitError("private"),
        ChatHistoryError("private"),
        ChatRequestConflictError("private"),
    ],
)
def test_unrecoverable_conversation_error_requires_new_conversation(error):
    class FailingService:
        def chat(self, request):
            raise error

    ui = ChatUI(FailingService)
    session = ChatSession()
    output = list(ui.send("Olá", session))[-1]
    assert session.blocked
    assert not session.busy
    assert session.pending is None
    assert "private" not in output[6]
    assert not output[3]["interactive"]
    assert not output[4]["interactive"]
    assert output[5]["interactive"]
    assert not ui.new_conversation(session)[0].blocked


def test_busy_session_rejects_extra_send_retry_and_reset():
    agent = RecordingAgent()
    ui = ChatUI(lambda: ChatService(agent))
    session = ChatSession()
    first = ui.send("Olá", session)
    next(first)
    assert session.busy
    assert session.pending is not None
    assert not agent.calls
    list(ui.send("Duplicada", session))
    list(ui.retry(session))
    assert ui.new_conversation(session)[0] is session
    assert len(session.messages) == 1
    assert not agent.calls
    list(first)
    assert len(agent.calls) == 1
    assert not session.busy


def test_independent_browser_sessions_do_not_share_state():
    agent = RecordingAgent()
    ui = ChatUI(lambda: ChatService(agent))
    first, second = ui.new_conversation(None)[0], ui.new_conversation(None)[0]
    list(ui.send("Primeira", first))
    assert first.conversation_id != second.conversation_id
    assert not second.messages
    assert second.pending is None


def test_gradio_event_processes_state_and_only_serializes_public_messages():
    agent = RecordingAgent()
    demo = build_chat_ui(lambda: ChatService(agent))
    state = SessionState(demo)
    send = next(fn for fn in demo.fns.values() if fn.fn.__name__ == "send")
    state_id = next(
        key for key, component in demo.blocks.items() if isinstance(component, gr.State)
    )

    async def run():
        result = await demo.process_api(send, ["Olá", None], state=state)
        assert result["is_generating"]
        assert state[state_id].current.pending is not None
        assert not agent.calls
        while result["is_generating"]:
            result = await demo.process_api(
                send, ["Olá", None], state=state, iterator=result["iterator"]
            )
            assert "private" not in json.dumps(result["data"])

    asyncio.run(run())
    assert len(agent.calls) == 1
    assert state[state_id].current.messages[-1]["content"] == "Resposta pública"


def test_switching_conversations_preserves_separate_contexts(engine):
    agent = RecordingAgent()
    service = ChatService(agent, session_factory=sessionmaker(engine))
    ui = ConversationUI(lambda: service)
    workspace = ChatWorkspace()
    first_id = workspace.active
    list(ui.send("Quero um violão", workspace))
    ui.new_conversation(workspace)
    second_id = workspace.active
    assert first_id != second_id
    assert not workspace.current.messages
    list(ui.send("Preciso de um teclado", workspace))
    assert not agent.calls[-1][1]
    output = ui.select(str(first_id), workspace)
    assert output[1][0]["content"] == "Quero um violão"
    assert len(agent.calls) == 2  # Alternar não chama o modelo.
    list(ui.send("Tem de nylon?", workspace))
    assert agent.calls[-1][0].conversation_id == first_id
    assert agent.calls[-1][1][0].content == "Quero um violão"
    assert all(m.content != "Preciso de um teclado" for m in agent.calls[-1][1])
    assert "private" not in json.dumps(ui.view(workspace, "")[1:])


def test_reload_restores_known_conversations_and_selection_from_database(engine):
    agent = RecordingAgent()
    service = ChatService(agent, session_factory=sessionmaker(engine))
    ui = ConversationUI(lambda: service)
    workspace = ChatWorkspace()
    first_id = workspace.active
    list(ui.send("Primeira conversa", workspace))
    ui.new_conversation(workspace)
    list(ui.send("Segunda conversa", workspace))
    ui.select(str(first_id), workspace)
    saved = ui.view(workspace, "")[8]
    unrelated = ChatRequest(message="Conversa de outro navegador")
    service.chat(unrelated)

    restored = ConversationUI(lambda: service).restore(saved)[0]
    assert restored is not workspace
    assert restored.active == first_id
    assert restored.conversations == workspace.conversations
    assert unrelated.conversation_id not in restored.conversations
    assert len(agent.calls) == 3  # Restaurar só lê o banco.
    assert set(saved) == {"ids", "active"}
    assert "Primeira conversa" not in json.dumps(saved)
    assert "private" not in json.dumps(ui.view(restored, "")[1:])


def test_opening_old_conversation_by_id_loads_public_messages_and_continues(engine):
    agent = RecordingAgent()
    service = ChatService(agent, session_factory=sessionmaker(engine))
    request = ChatRequest(message="Uma conversa anterior")
    service.chat(request)
    ui = ConversationUI(lambda: service)
    workspace = ChatWorkspace()
    output = ui.open_conversation(str(request.conversation_id), workspace)
    assert workspace.active == request.conversation_id
    assert output[1] == [
        {"role": "user", "content": "Uma conversa anterior"},
        {"role": "assistant", "content": "Resposta pública"},
    ]
    list(ui.send("Continue", workspace))
    assert agent.calls[-1][1][0].content == "Uma conversa anterior"
    selected = workspace.active
    assert "válido" in ui.open_conversation("não é UUID", workspace)[6]
    assert "não encontrada" in ui.open_conversation(str(uuid4()), workspace)[6]
    assert workspace.active == selected


def test_navigation_is_blocked_during_a_turn():
    agent = RecordingAgent()
    ui = ConversationUI(lambda: ChatService(agent))
    workspace = ChatWorkspace()
    first_id = workspace.active
    workspace.current.messages = [{"role": "user", "content": "Conversa anterior"}]
    ui.new_conversation(workspace)
    active = workspace.active
    sending = ui.send("Olá", workspace)
    output = next(sending)
    assert not output[9]["interactive"]
    assert not output[10]["interactive"]
    ui.select(str(first_id), workspace)
    ui.open_conversation(str(first_id), workspace)
    ui.new_conversation(workspace)
    assert workspace.active == active
    assert not agent.calls
    list(sending)
    assert len(agent.calls) == 1
    ui.select(str(first_id), workspace)
    assert workspace.active == first_id


def test_pending_retry_survives_switching_conversations(engine):
    agent = RecordingAgent()

    class LostResponse(ChatService):
        def __init__(self):
            super().__init__(agent, session_factory=sessionmaker(engine))
            self.requests = []

        def chat(self, request):
            self.requests.append(request.model_copy())
            turn = super().chat(request)
            if len(self.requests) == 1:
                raise SQLAlchemyError("private")
            return turn

    service = LostResponse()
    ui = ConversationUI(lambda: service)
    workspace = ChatWorkspace()
    first_id = workspace.active
    list(ui.send("Minha mensagem", workspace))
    pending = workspace.current.pending
    ui.new_conversation(workspace)
    list(ui.send("Outro assunto", workspace))
    ui.select(str(first_id), workspace)
    assert workspace.current.pending is pending
    list(ui.retry(workspace))
    assert service.requests[0] == service.requests[-1]
    assert len(agent.calls) == 2
    assert workspace.current.pending is None
    assert len(workspace.current.messages) == 2


def test_restore_handles_invalid_browser_data_and_sanitizes_database_failure():
    class FailingService:
        def load_conversations(self, ids):
            raise SQLAlchemyError("private-credentials")

    ui = ConversationUI(FailingService)
    fresh = ui.restore({"ids": ["invalid", None, {}], "active": "invalid"})[0]
    assert len(fresh.conversations) == 1
    conversation_id = str(uuid4())
    output = ui.restore({"ids": [conversation_id, conversation_id], "active": conversation_id})
    assert list(output[0].conversations) == [output[0].active]
    assert output[0].current.blocked
    assert "private" not in json.dumps(output[1:])
    assert output[8]["ids"] == [conversation_id]


def test_mounted_gradio_preserves_lifespan_and_http_api():
    lifecycle = []

    @asynccontextmanager
    async def lifespan(app):
        lifecycle.append("started")
        app.state.chat_service = ChatService()
        yield
        lifecycle.append("stopped")

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    app = mount_chat_ui(app)
    with TestClient(app) as client:
        assert lifecycle == ["started"]
        page = client.get("/ui/")
        assert page.status_code == 200
        assert "Empório da Música" in page.text
        assert client.get("/ui/config").status_code == 200
        assert client.get("/docs").status_code == 200
        response = client.post("/chat", json={"message": "Olá"})
        assert response.status_code == 200
        assert response.json()["status"] == "unavailable"
    assert lifecycle == ["started", "stopped"]
