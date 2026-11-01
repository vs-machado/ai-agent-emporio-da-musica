"""Contrato HTTP público e integração com a persistência das conversas."""

from threading import get_ident
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.models import ConversationTurn
from app.routes.chat import router
from app.schemas.chat import ChatResponse
from app.services.chat import ChatService, ChatTurn
from app.services.errors import ChatHistoryError, ChatHistoryLimitError, ChatRequestConflictError


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(router)
    app.state.chat_service = ChatService()
    with TestClient(app) as client:
        yield app, client


def test_chat_generates_ids_and_returns_public_response(api):
    _, client = api
    response = client.post("/chat", json={"message": "Olá"})
    assert response.status_code == 200
    body = response.json()
    assert UUID(body["conversation_id"])
    assert UUID(body["request_id"])
    assert body["status"] == "unavailable"
    assert set(body) == {
        "conversation_id",
        "request_id",
        "status",
        "message",
        "model",
        "used_fallback",
        "actions",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": "   "},
        {"message": "x" * 4001},
        {"message": "Olá", "conversation_id": "invalid"},
        {"message": "Olá", "request_id": "invalid"},
        {"message": "Olá", "role": "system"},
        {"message": "Olá", "history": []},
        {"message": "Olá", "messages": []},
    ],
)
def test_chat_rejects_invalid_or_client_controlled_history(api, payload):
    _, client = api
    assert client.post("/chat", json=payload).status_code == 422


@pytest.mark.parametrize("status", ["ok", "unavailable", "limit_reached"])
def test_chat_keeps_internal_transcript_private_and_runs_outside_event_loop(api, status):
    app, client = api
    event_loop_thread = client.portal.call(get_ident)

    class Agent:
        def run(self, request, history):
            assert get_ident() != event_loop_thread
            return ChatTurn(
                ChatResponse(
                    conversation_id=request.conversation_id,
                    request_id=request.request_id,
                    status=status,
                    message="Resposta pública",
                ),
                [
                    HumanMessage(content=request.message),
                    AIMessage(
                        content="raciocínio privado",
                        tool_calls=[
                            {
                                "name": "search_products",
                                "args": {"private": "argument"},
                                "id": "one",
                            }
                        ],
                    ),
                    ToolMessage(content="resultado privado", tool_call_id="one"),
                    AIMessage(content="Resposta pública"),
                ],
            )

    app.state.chat_service = ChatService(Agent())
    response = client.post("/chat", json={"message": "Olá"})
    assert response.status_code == 200
    assert response.json()["status"] == status
    assert response.json()["message"] == "Resposta pública"
    assert "privado" not in response.text
    assert "argument" not in response.text
    assert "tool_calls" not in response.text


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ChatRequestConflictError("private"), 409),
        (ChatHistoryLimitError("private"), 422),
        (ChatHistoryError("private"), 500),
        (SQLAlchemyError("private-database-credentials"), 503),
    ],
)
def test_chat_maps_errors_without_exposing_internal_details(api, error, code):
    app, client = api

    class FailingService:
        def chat(self, request):
            raise error

    app.state.chat_service = FailingService()
    response = client.post("/chat", json={"message": "Olá"})
    assert response.status_code == code
    assert "detail" in response.json()
    assert "private" not in response.text


def test_chat_persists_resumes_and_deduplicates_turns(api, engine):
    app, client = api

    class Agent:
        def run(self, request, history):
            text = f"Histórico: {len(history)} mensagens"
            return ChatTurn(
                ChatResponse(
                    conversation_id=request.conversation_id,
                    request_id=request.request_id,
                    status="ok",
                    message=text,
                ),
                [HumanMessage(content=request.message), AIMessage(content=text)],
            )

    app.state.chat_service = ChatService(Agent(), session_factory=sessionmaker(engine))
    payload = {"message": "Olá", "conversation_id": str(uuid4()), "request_id": str(uuid4())}
    first = client.post("/chat", json=payload)
    assert first.status_code == 200
    assert first.json()["message"] == "Histórico: 0 mensagens"
    assert client.post("/chat", json=payload).json() == first.json()
    assert client.post("/chat", json={**payload, "message": "Diferente"}).status_code == 409
    second = client.post(
        "/chat", json={"message": "Continue", "conversation_id": payload["conversation_id"]}
    )
    assert second.status_code == 200
    assert second.json()["message"] == "Histórico: 2 mensagens"
    app.state.chat_service.max_history_messages = 2
    assert (
        client.post(
            "/chat", json={"message": "Mais", "conversation_id": payload["conversation_id"]}
        ).status_code
        == 422
    )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 2


def test_chat_is_registered_in_application_openapi():
    from app.main import app

    operation = app.openapi()["paths"]["/chat"]["post"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ChatResponse"
    }
    assert {"409", "422", "500", "503"} <= operation["responses"].keys()
