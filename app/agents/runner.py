"""Agente padrão LangChain com limites e fallback por chamada de modelo."""

import json
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock
from uuid import UUID, uuid5

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, ToolException
from langchain_core.utils.function_calling import convert_to_openai_tool
from openai import OpenAIError
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.agents.models import AgentModels, ModelOutputError, is_recoverable
from app.agents.prompt import SYSTEM_PROMPT
from app.config import Settings
from app.schemas.chat import ChatAction, ChatRequest, ChatResponse
from app.services.chat import LIMIT_MESSAGE, UNAVAILABLE_MESSAGE, ChatTurn


@dataclass
class TurnContext:
    request_id: UUID
    model_calls: int = 0
    tool_calls: int = 0
    used_fallback: bool = False
    model_name: str | None = None
    status: str = "ok"
    actions: list[ChatAction] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)


def interrupted_message(context: TurnContext) -> str:
    message = LIMIT_MESSAGE if context.status == "limit_reached" else UNAVAILABLE_MESSAGE
    receipts = {
        action.reference
        for action in context.actions
        if action.name == "register_complaint"
        and action.status == "registered"
        and action.reference
    }
    if receipts:
        message += (
            " A reclamação foi registrada sob o protocolo "
            + ", ".join(sorted(receipts))
            + ". O envio à equipe não está integrado "
            "e não há garantia de retorno em 24 horas úteis."
        )
    return message


class AttendanceMiddleware(AgentMiddleware):
    def __init__(self, models: AgentModels, settings: Settings):
        self.models = models
        self.settings = settings

    def wrap_model_call(self, request, handler):
        """Limita rodadas e troca de provedor sem repetir a execução das tools.

        Args:
            request: Contexto e mensagens mantidos pelo agente padrão do LangChain.
            handler: Executa somente a próxima chamada de modelo.

        Returns:
            Resposta válida ou encerramento público sanitizado. O fallback recebe
            o mesmo estado e permanece selecionado até o término deste turno.
        """
        context: TurnContext = request.runtime.context
        if context.status != "ok" or context.model_calls >= self.settings.chat_max_model_calls:
            if context.status == "ok":
                context.status = "limit_reached"
            return ModelResponse(result=[AIMessage(content=interrupted_message(context))])
        context.model_calls += 1
        use_fallback = context.used_fallback and self.models.fallback is not None
        model = self.models.fallback if use_fallback else self.models.primary
        context.model_name = self.models.fallback_name if use_fallback else self.models.primary_name
        model_tools = []
        for tool in request.tools:
            if isinstance(tool, BaseTool) and tool.name == "register_complaint":
                public_tool = deepcopy(convert_to_openai_tool(tool))
                schema = public_tool["function"]["parameters"]
                schema["properties"].pop("request_id", None)
                schema["required"] = [
                    name for name in schema.get("required", []) if name != "request_id"
                ]
                model_tools.append(public_tool)
            else:
                model_tools.append(tool)
        try:
            try:
                response = handler(request.override(model=model, tools=model_tools))
            except OpenAIError as exc:
                if use_fallback or self.models.fallback is None or not is_recoverable(exc):
                    raise
                context.used_fallback = True
                context.model_name = self.models.fallback_name
                response = handler(request.override(model=self.models.fallback, tools=model_tools))
            final = response.result[-1] if response.result else None
            if not isinstance(final, AIMessage) or final.invalid_tool_calls:
                raise ModelOutputError("Resposta inválida do modelo")
            if final.tool_calls and not request.tools:
                raise ModelOutputError("Modelo solicitou tools sem registro disponível")
            ids = [call["id"] for call in final.tool_calls]
            if not all(ids) or len(set(ids)) != len(ids):
                raise ModelOutputError("IDs de chamadas inválidos")
            if not final.tool_calls and not final.text.strip():
                raise ModelOutputError("Resposta sem texto público")
            if final.response_metadata.get("finish_reason") in {"length", "max_tokens"}:
                raise ModelOutputError("Resposta incompleta por limite de tokens")
            return response
        except (OpenAIError, ModelOutputError):
            context.status = "unavailable"
            return ModelResponse(result=[AIMessage(content=interrupted_message(context))])

    def wrap_tool_call(self, request, handler):
        """Aplica orçamento concorrente e chave de reclamação controlada pela aplicação.

        Args:
            request: Chamada emitida pelo modelo e tool registrada no agente.
            handler: Executa a tool, incluindo sua validação de argumentos.

        Returns:
            Resultado pareado à chamada. Operações concluídas são registradas no
            contexto local para preservar referências em uma eventual interrupção.
        """
        context: TurnContext = request.runtime.context
        with context.lock:
            if context.tool_calls >= self.settings.chat_max_tool_calls:
                context.status = "limit_reached"
                return self._tool_error(request, "limit_reached", "Limite de consultas atingido.")
            context.tool_calls += 1
        if request.tool is None:
            return self._tool_error(request, "invalid_input", "Tool não disponível.")
        if request.tool_call["name"] == "register_complaint":
            call = {
                **request.tool_call,
                "args": {
                    **request.tool_call["args"],
                    "request_id": str(uuid5(context.request_id, "register_complaint")),
                },
            }
            request = request.override(tool_call=call)
        try:
            result = handler(request)
        except (ValidationError, ToolException):
            result = self._tool_error(request, "invalid_input", "Confira os argumentos da tool.")
        except (SQLAlchemyError, OSError, TimeoutError):
            result = self._tool_error(request, "unavailable", "Consulta indisponível no momento.")
        if isinstance(result, ToolMessage):
            try:
                payload = json.loads(result.content)
            except (ValueError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            action = ChatAction(
                name=request.tool_call["name"],
                status=str(payload.get("status", "unknown")),
                reference=payload.get("complaint_id")
                if payload.get("status") == "registered"
                else None,
            )
            with context.lock:
                context.actions.append(action)
        return result

    @staticmethod
    def _tool_error(request, status: str, message: str) -> ToolMessage:
        return ToolMessage(
            content=json.dumps({"status": status, "message": message}, ensure_ascii=False),
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
            status="error",
        )


class LangChainChatAgent:
    def __init__(self, models: AgentModels, tools: Sequence[BaseTool], settings: Settings):
        self.models = models
        self.settings = settings
        self.graph = create_agent(
            model=models.primary,
            tools=list(tools),
            system_prompt=SYSTEM_PROMPT,
            middleware=[AttendanceMiddleware(models, settings)],
            context_schema=TurnContext,
            name="emporio_atendimento",
        )

    def run(self, request: ChatRequest, history: Sequence[BaseMessage]) -> ChatTurn:
        """Executa apenas o turno atual, mantendo o histórico de entrada intacto.

        Args:
            request: Mensagem e chave estável, também usada para idempotência de reclamação.
            history: Mensagens internas de turnos anteriores, incluindo pares de tools.

        Returns:
            Resposta e todas as novas mensagens. Falhas do provedor preservam resultados
            de operações já executadas; o fallback não reexecuta o turno nem as tools.
        """
        context = TurnContext(request_id=request.request_id)
        messages = [m.model_copy(deep=True) for m in history]
        messages.append(HumanMessage(content=request.message))
        result = self.graph.invoke(
            {"messages": messages},
            context=context,
            config={"recursion_limit": 4 * self.settings.chat_max_model_calls + 10},
        )
        new_messages = result["messages"][len(history) :]
        final = new_messages[-1]
        return ChatTurn(
            ChatResponse(
                request_id=request.request_id,
                status=context.status,
                message=final.text,
                model=context.model_name,
                used_fallback=context.used_fallback,
                actions=context.actions,
            ),
            new_messages,
        )
