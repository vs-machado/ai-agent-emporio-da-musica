"""Autenticação real e isolamento entre contas, pedidos, conversas e fila Gradio."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import gradio as gr
import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from gradio.state_holder import SessionState
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.ingestion.loader import ingest
from app.models import Customer, Order, User
from app.routes.auth import Credentials, SameOriginMiddleware
from app.routes.auth import router as auth_router
from app.routes.chat import router as chat_router
from app.routes.ui_access import UIAccessMiddleware
from app.schemas.chat import ChatRequest
from app.schemas.orders import ComplaintRegistration, OrderLookup, RefundQuote
from app.seed_users import seed_users
from app.services.auth import COOKIE_NAME, AuthService, current_principal, passwords
from app.services.chat import ChatService, ConversationAccessError
from app.services.orders import OrderService
from app.ui import AuthenticatedConversationUI, ChatWorkspace, build_chat_ui, mount_chat_ui

PASSWORD = "Senha-de-teste-123"


@pytest.fixture
def auth_app(engine):
    ingest(engine)
    sessions = sessionmaker(engine)
    settings = Settings(auth_jwt_secret="s" * 48, auth_cookie_secure=False)
    app = FastAPI()
    app.state.auth_service = AuthService(sessions, settings)
    app.state.chat_service = ChatService(session_factory=sessions)
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.add_middleware(UIAccessMiddleware)
    app.add_middleware(SameOriginMiddleware)
    app = mount_chat_ui(app)
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        yield app, client, sessions


def login(client, email, password=PASSWORD):
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response


def test_credentials_preserve_password_whitespace():
    credentials = Credentials(email=" A@EXAMPLE.TEST ", password=" senha-de-teste ")
    assert credentials.email == "a@example.test"
    assert credentials.password == " senha-de-teste "


def test_register_login_logout_cookie_and_no_automatic_customer_link(auth_app):
    app, client, sessions = auth_app
    with sessions() as session:
        existing_email = session.scalar(select(Customer.email).limit(1))
    credentials = {"email": existing_email.upper(), "password": PASSWORD}
    assert client.post("/chat", json={"message": "Olá"}).status_code == 401
    assert client.get("/ui/", follow_redirects=False).headers["location"] == "/auth"
    assert client.get("/ui/config").status_code == 401
    assert client.post("/auth/register", json=credentials).status_code == 201
    assert client.post("/auth/register", json=credentials).status_code == 409
    assert (
        client.post("/auth/login", json={**credentials, "password": "senha-incorreta"}).status_code
        == 401
    )
    response = login(client, existing_email)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert client.get("/auth/me").json()["customer_id"] is None
    with sessions() as session:
        user = session.scalar(select(User).where(User.email == existing_email.casefold()))
        assert user.password_hash != PASSWORD
        assert passwords.verify(PASSWORD, user.password_hash)
    token = client.cookies.get(COOKIE_NAME)
    assert client.get("/ui/").status_code == 200
    assert client.post("/auth/logout", follow_redirects=False).status_code == 303
    assert client.get("/auth/me").status_code == 401
    assert app.state.auth_service.authenticate(token) is None


def test_expired_tampered_and_unknown_user_tokens_are_rejected(auth_app):
    app, client, _ = auth_app
    service = app.state.auth_service
    service.register("a@example.test", PASSWORD)
    token = service.login("a@example.test", PASSWORD)
    claims = jwt.decode(token, service.secret, algorithms=["HS256"], audience="emporio-atendimento")
    for changes in (
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
        {"sub": str(uuid4())},
        {"aud": "another-app"},
        {"ver": 100},
    ):
        invalid = jwt.encode({**claims, **changes}, service.secret, algorithm="HS256")
        assert service.authenticate(invalid) is None
    assert service.authenticate(token + "tampered") is None
    client.cookies.set(COOKIE_NAME, "invalid")
    assert client.get("/auth/me").status_code == 401


def test_csrf_rejects_cross_origin_and_missing_origin(auth_app):
    _, client, _ = auth_app
    payload = {"email": "a@example.test", "password": PASSWORD}
    for origin in ("https://evil.example", "null", "", "http://[invalid"):
        assert (
            client.post("/auth/register", json=payload, headers={"Origin": origin}).status_code
            == 403
        )
    assert client.post("/auth/register", json=payload).status_code == 201


def test_accounts_cannot_read_or_append_each_others_conversations(auth_app):
    app, client, sessions = auth_app
    accounts = seed_users(sessions, PASSWORD)
    login(client, accounts[0]["email"])
    first = client.post("/chat", json={"message": "Conversa privada da conta A"})
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]
    assert conversation_id in client.get("/conversations").json()
    # Mesmo dono continua vendo o histórico em uma nova sessão de login.
    login(client, accounts[0]["email"])
    assert conversation_id in client.get("/conversations").json()
    login(client, accounts[1]["email"])
    assert client.get("/conversations").json() == {}
    denied = client.post(
        "/chat",
        json={
            "message": "Continue",
            "conversation_id": conversation_id,
            "request_id": first.json()["request_id"],
        },
    )
    assert denied.status_code == 404
    assert "privada" not in denied.text
    user_b = app.state.auth_service.authenticate(client.cookies.get(COOKIE_NAME))
    assert app.state.chat_service.load_conversations(user=user_b) == {}
    assert app.state.chat_service.load_conversations([UUID(conversation_id)], user=user_b) == {}
    with pytest.raises(ConversationAccessError):
        app.state.chat_service.chat(
            ChatRequest(conversation_id=conversation_id, message="Invasão"), user=user_b
        )


def test_orders_refunds_and_complaints_enforce_authenticated_customer(auth_app):
    app, client, sessions = auth_app
    accounts = seed_users(sessions, PASSWORD)
    orders = OrderService(sessions)
    order_id = accounts[0]["orders"][0]
    with sessions() as session:
        email = session.get(Customer, accounts[0]["customer_id"]).email
    for account in accounts:
        login(client, account["email"])
        principal = app.state.auth_service.authenticate(client.cookies.get(COOKIE_NAME))
        token = current_principal.set(principal)
        try:
            status = orders.get_order_status(OrderLookup(order_id=order_id, email=email))
            refund = orders.calculate_refund(RefundQuote(order_id=order_id))
            complaint = orders.register_complaint(
                ComplaintRegistration(
                    request_id=uuid4(),
                    order_id=order_id,
                    contact_email=email,
                    message="Quero registrar uma reclamação sobre meu pedido.",
                )
            )
        finally:
            current_principal.reset(token)
        if account == accounts[0]:
            assert status["status"] == "ok"
            assert refund["status"] == "requires_confirmation"
            assert complaint["status"] == "registered"
        else:
            assert status["status"] == refund["status"] == complaint["status"] == "not_found"
    assert (
        orders.get_order_status(OrderLookup(order_id=order_id, email=email))["status"]
        == "not_found"
    )


def test_gradio_queue_session_cannot_be_reused_by_other_account(auth_app):
    _, client, sessions = auth_app
    accounts = seed_users(sessions, PASSWORD)
    login(client, accounts[0]["email"])

    # Uma rota inexistente exercita a barreira sem abrir um stream SSE infinito.
    path = "/ui/session-probe?session_hash=private-session-a"
    assert client.get(path).status_code == 404
    login(client, accounts[1]["email"])
    denied = client.get("/ui/gradio_api/queue/data?session_hash=private-session-a")
    assert denied.status_code == 404
    assert denied.json()["detail"] == "Sessão não encontrada."
    assert (
        client.post(
            "/ui/gradio_api/queue/join",
            json={
                "session_hash": "private-session-a",
                "data": [],
                "fn_index": 0,
            },
        ).status_code
        == 404
    )


def test_gradio_callbacks_reset_state_after_account_switch(auth_app):
    app, client, sessions = auth_app
    accounts = seed_users(sessions, PASSWORD)
    login(client, accounts[1]["email"])
    principal = app.state.auth_service.authenticate(client.cookies.get(COOKIE_NAME))
    request = Request(
        {
            "type": "http",
            "app": app,
            "headers": [(b"cookie", f"{COOKIE_NAME}={client.cookies.get(COOKIE_NAME)}".encode())],
        }
    )
    controller = AuthenticatedConversationUI(lambda: app.state.chat_service)
    old_workspace = ChatWorkspace(user_id=uuid4())
    old_workspace.current.messages = [{"role": "user", "content": "Mensagem de outra conta"}]
    _, workspace = controller.scope(gr.Request(request=request), old_workspace)
    assert workspace.user_id == principal.user_id
    assert workspace is not old_workspace
    assert not workspace.current.messages


def test_seed_is_idempotent_and_preserves_existing_orders(auth_app):
    _, _, sessions = auth_app
    with sessions() as session:
        before = list(session.scalars(select(Order.order_id).order_by(Order.order_id)))
        expected = {
            customer.email.strip().casefold(): customer.customer_id
            for customer in session.scalars(select(Customer).order_by(Customer.customer_id.desc()))
        }
    accounts = seed_users(sessions, PASSWORD)
    assert seed_users(sessions, "outra-senha-nao-substitui") == accounts
    with sessions() as session:
        assert list(session.scalars(select(Order.order_id).order_by(Order.order_id))) == before
        user = session.scalar(select(User).where(User.email == accounts[0]["email"]))
        assert passwords.verify(PASSWORD, user.password_hash)
        assert {user.email: user.customer_id for user in session.scalars(select(User))} == expected
    assert {account["email"]: account["customer_id"] for account in accounts} == expected
    assert any(account["orders"] for account in accounts)
    assert any(not account["orders"] for account in accounts)


def test_seed_migrates_legacy_demo_login_preserving_password_and_history(auth_app):
    app, client, sessions = auth_app
    legacy_password = "Senha-legada-123"
    with sessions.begin() as session:
        customers = session.scalars(select(Customer).order_by(Customer.customer_id).limit(2)).all()
        for label, customer in zip(("a", "b"), customers, strict=True):
            session.add(
                User(
                    email=f"demo.{label}@example.test",
                    customer_id=customer.customer_id,
                    password_hash=passwords.hash(legacy_password),
                )
            )
        session.add(
            User(email="demo.c@example.test", password_hash=passwords.hash(legacy_password))
        )
        customer_email = customers[0].email
    login(client, "demo.a@example.test", legacy_password)
    original_user_id = client.get("/auth/me").json()["user_id"]
    conversation_id = client.post(
        "/chat", json={"message": "Histórico anterior ao novo seed"}
    ).json()["conversation_id"]
    seed_users(sessions, PASSWORD)
    login(client, customer_email, legacy_password)
    assert client.get("/auth/me").json()["user_id"] == original_user_id
    assert conversation_id in client.get("/conversations").json()
    assert app.state.auth_service.login(customer_email, PASSWORD) is None
    assert app.state.auth_service.login("demo.a@example.test", legacy_password) is None
    assert app.state.auth_service.login("demo.c@example.test", legacy_password) is not None
    assert len(seed_users(sessions, PASSWORD)) == 49


def test_seed_rejects_existing_public_account_and_rolls_back(auth_app):
    app, _, sessions = auth_app
    with sessions() as session:
        email = session.scalar(select(Customer.email).where(Customer.customer_id == 2))
    account = app.state.auth_service.register(email, PASSWORD)
    with pytest.raises(ValueError, match="outro vínculo"):
        seed_users(sessions, PASSWORD)
    with sessions() as session:
        users = session.scalars(select(User)).all()
        assert len(users) == 1
        assert users[0].user_id == account.user_id
        assert users[0].customer_id is None
        assert passwords.verify(PASSWORD, users[0].password_hash)


def test_seed_deduplicates_normalized_emails_without_combining_customers(auth_app):
    _, _, sessions = auth_app
    with sessions.begin() as session:
        customer = session.get(Customer, 1)
        session.get(Customer, 2).email = f" {customer.email.upper()} "
        email = customer.email
    accounts = seed_users(sessions, PASSWORD)
    account = next(account for account in accounts if account["email"] == email)
    assert account["customer_id"] == 1
    assert account["duplicate_customer_ids"] == [2]
    with sessions() as session:
        assert session.scalar(select(User).where(User.customer_id == 2)) is None
        assert account["orders"] == list(
            session.scalars(
                select(Order.order_id).where(Order.customer_id == 1).order_by(Order.order_id)
            )
        )
    assert len(accounts) == 48


def test_authenticated_gradio_event_persists_and_restores_history(auth_app):
    app, client, sessions = auth_app
    accounts = seed_users(sessions, PASSWORD)
    login(client, accounts[0]["email"])
    token = client.cookies.get(COOKIE_NAME)
    request = Request(
        {
            "type": "http",
            "app": app,
            "headers": [
                (b"cookie", f"{COOKIE_NAME}={token}".encode()),
            ],
        }
    )
    gr_request = gr.Request(request=request)
    demo = build_chat_ui(lambda: app.state.chat_service)
    state = SessionState(demo)
    send = next(fn for fn in demo.fns.values() if fn.fn.__name__ == "send")
    state_id = next(
        key for key, component in demo.blocks.items() if isinstance(component, gr.State)
    )

    async def run():
        result = await demo.process_api(
            send, ["Mensagem autenticada", None], state=state, request=gr_request
        )
        while result["is_generating"]:
            result = await demo.process_api(
                send,
                ["Mensagem autenticada", None],
                state=state,
                request=gr_request,
                iterator=result["iterator"],
            )

    asyncio.run(run())
    workspace = state[state_id]
    assert workspace.user_id is not None
    assert workspace.current.messages[0]["content"] == "Mensagem autenticada"
    restored = AuthenticatedConversationUI(lambda: app.state.chat_service).restore(
        None, gr_request
    )[0]
    assert restored.current.messages == workspace.current.messages
    assert str(workspace.active) in client.get("/conversations").json()
