"""Verificação de disponibilidade da aplicação e do banco."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Infraestrutura"])


@router.get("/health", responses={503: {"description": "Banco indisponível"}})
def health(request: Request):
    """Verifica se a API consegue consultar o banco de dados."""
    try:
        with request.app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Falha na verificação de saúde do banco de dados")
        return JSONResponse(
            status_code=503, content={"status": "unavailable", "database": "unavailable"}
        )
    return {"status": "ok", "database": "ok"}
