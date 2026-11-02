"""Atendimento HTTP com histórico controlado exclusivamente pelo backend."""

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from app.routes.auth import CurrentUser
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat import ConversationAccessError
from app.services.errors import ChatHistoryError, ChatHistoryLimitError, ChatRequestConflictError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Atendimento"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        401: {"description": "Login obrigatório"},
        403: {"description": "Origem da solicitação inválida"},
        404: {"description": "Conversa não disponível para essa conta"},
        409: {"description": "ID do turno já utilizado com outra mensagem"},
        422: {"description": "Entrada inválida ou limite do histórico excedido"},
        500: {"description": "Histórico interno inválido"},
        503: {"description": "Persistência do atendimento indisponível"},
    },
)
def chat(payload: ChatRequest, request: Request, user: CurrentUser) -> ChatResponse:
    """Executa um turno e retorna apenas sua resposta pública.

    Sem conversation_id, inicia uma conversa. Reutilize o UUID para continuá-la
    e o mesmo request_id e texto para recuperar um turno já salvo. Os estados
    unavailable e limit_reached também representam turnos concluídos (HTTP 200).
    A função síncrona roda na thread pool do FastAPI, fora do event loop.
    """
    try:
        return request.app.state.chat_service.chat(payload, user=user).response
    except ConversationAccessError as exc:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.") from exc
    except ChatRequestConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="request_id já utilizado com outra mensagem nesta conversa.",
        ) from exc
    except ChatHistoryLimitError as exc:
        raise HTTPException(
            status_code=422,
            detail="O histórico excede o limite de mensagens. Inicie uma nova conversa.",
        ) from exc
    except ChatHistoryError as exc:
        logger.error("Histórico inválido no atendimento")
        raise HTTPException(
            status_code=500,
            detail="Não foi possível carregar ou salvar o histórico do atendimento.",
        ) from exc
    except SQLAlchemyError as exc:
        logger.error("Falha de persistência no atendimento")
        raise HTTPException(
            status_code=503,
            detail="O atendimento está temporariamente indisponível. Tente novamente em instantes.",
        ) from exc


@router.get("/conversations")
def conversations(request: Request, user: CurrentUser, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return request.app.state.chat_service.load_conversations(user=user)
