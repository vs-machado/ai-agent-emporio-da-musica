"""Chat Gradio com estado visual separado do histórico interno do agente."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import gradio as gr
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from app.routes.auth import get_current_user
from app.schemas.chat import ChatRequest
from app.services.auth import COOKIE_NAME
from app.services.chat import ChatService, ConversationAccessError, UserChatService
from app.services.errors import ChatHistoryError, ChatHistoryLimitError, ChatRequestConflictError

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    conversation_id: UUID = field(default_factory=uuid4)
    messages: list[dict[str, str]] = field(default_factory=list)
    pending: ChatRequest | None = None
    busy: bool = False
    blocked: bool = False


@dataclass
class ChatWorkspace:
    user_id: UUID | None = None
    active: UUID = field(default_factory=uuid4)
    conversations: dict[UUID, ChatSession] = field(default_factory=dict)

    def __post_init__(self):
        self.conversations.setdefault(self.active, ChatSession(conversation_id=self.active))

    @property
    def current(self) -> ChatSession:
        return self.conversations[self.active]


class ChatUI:
    def __init__(self, service: Callable[[], ChatService]):
        # Resolvido no callback, depois que o lifespan prepara as dependências.
        self.service = service

    @staticmethod
    def view(session: ChatSession, status: str):
        ready = not session.busy and not session.blocked and session.pending is None
        return (
            session,
            [dict(message) for message in session.messages],
            gr.update(value="", interactive=ready),
            gr.update(interactive=ready),
            gr.update(interactive=not session.busy and session.pending is not None),
            gr.update(interactive=not session.busy),
            status,
            str(session.conversation_id),
        )

    def new_conversation(self, session: ChatSession | None):
        if session is not None and session.busy:
            return self.view(session, "Aguarde o atendimento em andamento.")
        return self.view(ChatSession(), "Envie uma mensagem para começar.")

    def send(self, message: str, session: ChatSession | None, *, retry: bool = False):
        session = session if session is not None else ChatSession()
        if session.busy or session.blocked:
            yield self.view(session, "Aguarde o atendimento ou inicie uma nova conversa.")
            return
        if retry:
            if session.pending is None:
                yield self.view(session, "Não há mensagem pendente para tentar novamente.")
                return
        elif session.pending is not None:
            yield self.view(
                session, "Tente novamente a mensagem pendente ou inicie outra conversa."
            )
            return
        else:
            try:
                session.pending = ChatRequest(
                    conversation_id=session.conversation_id, message=message
                )
            except ValidationError:
                yield self.view(session, "Digite uma mensagem com 1 a 4.000 caracteres.")
                return
            session.messages.append({"role": "user", "content": session.pending.message})

        session.busy = True
        try:
            # Publica os IDs e desabilita os controles antes de iniciar modelo/tools.
            yield self.view(session, "Consultando o atendimento…")
            response = self.service().chat(session.pending).response
            session.messages.append({"role": "assistant", "content": response.message})
            session.pending = None
            status = {
                "ok": "Atendimento concluído.",
                "unavailable": "Atendimento indisponível. Você pode enviar outra mensagem.",
                "limit_reached": "Limite de consultas atingido. Tente uma pergunta mais simples.",
            }[response.status]
        except ChatHistoryLimitError:
            session.pending = None
            session.blocked = True
            status = "O histórico atingiu o limite de mensagens. Inicie uma nova conversa."
        except (ChatHistoryError, ChatRequestConflictError, ConversationAccessError):
            session.pending = None
            session.blocked = True
            status = "Não foi possível continuar este atendimento. Inicie uma nova conversa."
            logger.error("Conversa inválida na interface de atendimento")
        except Exception:
            # Preserva a chave inclusive quando a gravação terminou, mas a resposta falhou.
            status = "Não foi possível concluir a solicitação. Clique em Tentar novamente."
            logger.error("Falha na interface de atendimento")
        finally:
            session.busy = False
        yield self.view(session, status)

    def retry(self, session: ChatSession | None):
        yield from self.send("", session, retry=True)


class ConversationUI:
    """Navegação por conversa; IDs no navegador, mensagens públicas no banco."""

    def __init__(self, service: Callable[[], ChatService]):
        self.chat = ChatUI(service)

    def view(self, workspace: ChatWorkspace, status: str):
        output = self.chat.view(workspace.current, status)
        choices = []
        for conversation_id, session in reversed(workspace.conversations.items()):
            title = (
                " ".join(session.messages[0]["content"].split())[:60]
                if session.messages
                else "Nova conversa"
            )
            choices.append((f"{title} · {str(conversation_id)[:8]}", str(conversation_id)))
        return (
            workspace,
            *output[1:],
            {
                "ids": [str(conversation_id) for conversation_id in workspace.conversations],
                "active": str(workspace.active),
            },
            gr.update(
                choices=choices, value=str(workspace.active), interactive=not workspace.current.busy
            ),
            gr.update(interactive=not workspace.current.busy),
        )

    def restore(self, saved: dict | None):
        ids = []
        if isinstance(saved, dict) and isinstance(saved.get("ids"), list):
            for value in saved["ids"]:
                try:
                    conversation_id = UUID(str(value))
                except ValueError:
                    continue
                if conversation_id not in ids:
                    ids.append(conversation_id)
        if not ids:
            return self.view(ChatWorkspace(), "Envie uma mensagem para começar.")
        active = next((value for value in ids if str(value) == saved.get("active")), ids[-1])
        workspace = ChatWorkspace(
            active=active,
            conversations={value: ChatSession(conversation_id=value) for value in ids},
        )
        try:
            histories = self.chat.service().load_conversations(ids)
            for conversation_id, messages in histories.items():
                workspace.conversations[conversation_id].messages = messages
        except Exception:
            logger.error("Falha ao recuperar conversas na interface")
            for session in workspace.conversations.values():
                session.blocked = True
            return self.view(
                workspace, "Não foi possível carregar o histórico. Tente reabrir a página."
            )
        return self.view(workspace, "Conversa recuperada. Você pode continuar o atendimento.")

    def new_conversation(self, workspace: ChatWorkspace | None):
        workspace = workspace if workspace is not None else ChatWorkspace()
        if workspace.current.busy:
            return self.view(workspace, "Aguarde o atendimento em andamento.")
        if workspace.current.messages or workspace.current.blocked:
            session = ChatSession()
            workspace.active = session.conversation_id
            workspace.conversations[session.conversation_id] = session
        return self.view(workspace, "Envie uma mensagem para começar.")

    def select(self, conversation_id: str, workspace: ChatWorkspace | None):
        workspace = workspace if workspace is not None else ChatWorkspace()
        if workspace.current.busy:
            return self.view(workspace, "Aguarde o atendimento em andamento.")
        try:
            selected = UUID(conversation_id)
        except (ValueError, TypeError, AttributeError):
            return self.view(workspace, "Selecione uma conversa da lista.")
        if selected in workspace.conversations:
            workspace.active = selected
            return self.view(workspace, "Conversa selecionada.")
        return self.view(workspace, "Conversa não encontrada nesta lista.")

    def open_conversation(self, conversation_id: str, workspace: ChatWorkspace | None):
        workspace = workspace if workspace is not None else ChatWorkspace()
        if workspace.current.busy:
            return self.view(workspace, "Aguarde o atendimento em andamento.")
        try:
            selected = UUID(conversation_id.strip())
        except (ValueError, TypeError, AttributeError):
            return self.view(workspace, "Informe um ID de conversa válido.")
        if selected in workspace.conversations:
            return self.select(str(selected), workspace)
        try:
            messages = self.chat.service().load_conversations([selected]).get(selected)
        except Exception:
            logger.error("Falha ao abrir conversa na interface")
            return self.view(workspace, "Não foi possível carregar a conversa. Tente novamente.")
        if messages is None:
            return self.view(workspace, "Conversa não encontrada.")
        workspace.conversations[selected] = ChatSession(conversation_id=selected, messages=messages)
        workspace.active = selected
        return self.view(workspace, "Conversa recuperada. Você pode continuar o atendimento.")

    def send(self, message: str, workspace: ChatWorkspace | None):
        workspace = workspace if workspace is not None else ChatWorkspace()
        for output in self.chat.send(message, workspace.current):
            yield self.view(workspace, output[6])

    def retry(self, workspace: ChatWorkspace | None):
        workspace = workspace if workspace is not None else ChatWorkspace()
        for output in self.chat.retry(workspace.current):
            yield self.view(workspace, output[6])


class AuthenticatedConversationUI:
    """Resolve a identidade novamente em cada evento, inclusive após troca de conta."""

    def __init__(self, service, authenticate=get_current_user):
        self.service = service
        self.authenticate = authenticate

    def scope(self, request: gr.Request, workspace=None):
        user = self.authenticate(request.request)
        controller = ConversationUI(lambda: UserChatService(self.service(), user))
        if workspace is None or workspace.user_id != user.user_id:
            workspace = ChatWorkspace(user_id=user.user_id)
        return controller, workspace

    def restore(self, saved: dict | None, request: gr.Request):
        controller, workspace = self.scope(request)
        try:
            histories = controller.chat.service().load_conversations()
        except Exception:
            logger.error("Falha ao recuperar conversas autenticadas")
            return controller.view(
                workspace, "Não foi possível recuperar as conversas. Reabra a página."
            )
        if histories:
            workspace.conversations = {
                key: ChatSession(conversation_id=key, messages=messages)
                for key, messages in histories.items()
            }
            workspace.active = next(reversed(histories))
        return controller.view(workspace, "Envie uma mensagem para continuar o atendimento.")

    def send(self, message: str, workspace: ChatWorkspace | None, request: gr.Request):
        controller, workspace = self.scope(request, workspace)
        yield from controller.send(message, workspace)

    def retry(self, workspace: ChatWorkspace | None, request: gr.Request):
        controller, workspace = self.scope(request, workspace)
        yield from controller.retry(workspace)

    def new_conversation(self, workspace: ChatWorkspace | None, request: gr.Request):
        controller, workspace = self.scope(request, workspace)
        return controller.new_conversation(workspace)

    def select(self, conversation_id: str, workspace: ChatWorkspace | None, request: gr.Request):
        controller, workspace = self.scope(request, workspace)
        return controller.select(conversation_id, workspace)

    def open_conversation(
        self, conversation_id: str, workspace: ChatWorkspace | None, request: gr.Request
    ):
        controller, workspace = self.scope(request, workspace)
        return controller.open_conversation(conversation_id, workspace)


def build_chat_ui(
    service: Callable[[], ChatService],
    *,
    authenticated: bool = True,
    authenticate=get_current_user,
) -> gr.Blocks:
    controller = (
        AuthenticatedConversationUI(service, authenticate)
        if authenticated
        else ConversationUI(service)
    )
    with gr.Blocks(title="Empório da Música — Atendimento", analytics_enabled=False) as demo:
        gr.Markdown(
            "# Empório da Música\nOlá! Posso ajudar com instrumentos, pedidos e políticas da loja."
        )
        gr.HTML(
            '<form action="/auth/logout" method="post">'
            '<button type="submit">Sair da conta</button></form>'
        )
        session = gr.State()
        # O índice de conversas é carregado do banco pela identidade autenticada.
        browser = gr.State({"ids": [], "active": None})
        with gr.Sidebar(label="Conversas", open=True):
            new = gr.Button("+ Nova conversa", variant="primary")
            conversations = gr.Radio(choices=[], label="Suas conversas", interactive=True)
            with gr.Accordion("Retomar conversa pelo ID", open=False):
                saved_id = gr.Textbox(label="ID de uma conversa anterior")
                open_conversation = gr.Button("Abrir conversa")
        conversation_id = gr.Textbox(label="ID da conversa", interactive=False)
        chatbot = gr.Chatbot(
            label="Atendimento",
            height=450,
            buttons=["copy"],
            feedback_options=(),
            allow_tags=False,
        )
        message = gr.Textbox(
            label="Sua mensagem",
            placeholder="Como podemos ajudar?",
            max_length=4000,
            lines=2,
        )
        with gr.Row():
            send = gr.Button("Enviar", variant="primary")
            retry = gr.Button("Tentar novamente", interactive=False)
        status = gr.Textbox(label="Estado do atendimento", interactive=False)
        outputs = [
            session,
            chatbot,
            message,
            send,
            retry,
            new,
            status,
            conversation_id,
            browser,
            conversations,
            open_conversation,
        ]
        for event in (send.click, message.submit):
            event(
                controller.send,
                inputs=[message, session],
                outputs=outputs,
                concurrency_limit=None,
                api_visibility="private",
            )
        retry.click(
            controller.retry,
            inputs=[session],
            outputs=outputs,
            concurrency_limit=None,
            api_visibility="private",
        )
        new.click(
            controller.new_conversation,
            inputs=[session],
            outputs=outputs,
            concurrency_limit=None,
            api_visibility="private",
        )
        conversations.input(
            controller.select,
            inputs=[conversations, session],
            outputs=outputs,
            concurrency_limit=None,
            api_visibility="private",
        )
        for event in (open_conversation.click, saved_id.submit):
            event(
                controller.open_conversation,
                inputs=[saved_id, session],
                outputs=outputs,
                concurrency_limit=None,
                api_visibility="private",
            )
        demo.load(
            controller.restore,
            inputs=[browser],
            outputs=outputs,
            api_visibility="private",
        )
    return demo.queue()


def mount_chat_ui(app: FastAPI) -> FastAPI:
    # Gradio é uma subaplicação: request.app não é a aplicação dona do lifespan.
    def identity(request: Request):
        return app.state.auth_service.authenticate(request.cookies.get(COOKIE_NAME))

    def require_identity(request: Request):
        user = identity(request)
        if user is None:
            raise HTTPException(status_code=401, detail="Entre na sua conta para continuar.")
        return user

    def gradio_identity(request: Request):
        user = identity(request)
        return str(user.user_id) if user else None

    return gr.mount_gradio_app(
        app,
        build_chat_ui(lambda: app.state.chat_service, authenticate=require_identity),
        path="/ui",
        footer_links=[],
        show_error=False,
        ssr_mode=False,
        enable_monitoring=False,
        auth_dependency=gradio_identity,
    )
