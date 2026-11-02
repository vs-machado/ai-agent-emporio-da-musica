"""Chat Gradio com estado visual separado do histórico interno do agente."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import gradio as gr
from fastapi import FastAPI
from pydantic import ValidationError

from app.schemas.chat import ChatRequest
from app.services.chat import ChatService
from app.services.errors import ChatHistoryError, ChatHistoryLimitError, ChatRequestConflictError

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    conversation_id: UUID = field(default_factory=uuid4)
    messages: list[dict[str, str]] = field(default_factory=list)
    pending: ChatRequest | None = None
    busy: bool = False
    blocked: bool = False


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
        except (ChatHistoryError, ChatRequestConflictError):
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


def build_chat_ui(service: Callable[[], ChatService]) -> gr.Blocks:
    controller = ChatUI(service)
    with gr.Blocks(title="Empório da Música — Atendimento", analytics_enabled=False) as demo:
        gr.Markdown(
            "# Empório da Música\nOlá! Posso ajudar com instrumentos, pedidos e políticas da loja."
        )
        session = gr.State()
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
            new = gr.Button("Nova conversa")
        status = gr.Textbox(label="Estado do atendimento", interactive=False)
        outputs = [session, chatbot, message, send, retry, new, status, conversation_id]
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
        demo.load(
            controller.new_conversation,
            inputs=[session],
            outputs=outputs,
            api_visibility="private",
        )
    return demo.queue()


def mount_chat_ui(app: FastAPI) -> FastAPI:
    return gr.mount_gradio_app(
        app,
        build_chat_ui(lambda: app.state.chat_service),
        path="/ui",
        footer_links=[],
        show_error=False,
        ssr_mode=False,
        enable_monitoring=False,
    )
