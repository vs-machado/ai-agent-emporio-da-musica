"""Ponto de entrada da API HTTP."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents.factory import build_chat_service
from app.config import Settings
from app.database import create_database_engine, create_session_factory
from app.rag.retrieval import LlamaIndexPolicyRetriever
from app.routes.chat import router as chat_router
from app.routes.health import router as health_router
from app.tools import build_tools
from app.ui import mount_chat_ui


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    engine = create_database_engine(settings)
    app.state.session_factory = create_session_factory(engine)
    try:
        app.state.policy_retriever = LlamaIndexPolicyRetriever(engine, settings)
        app.state.tools = build_tools(app.state.session_factory, app.state.policy_retriever)
        app.state.chat_service = build_chat_service(
            settings, app.state.tools, session_factory=app.state.session_factory
        )
        yield
    finally:
        engine.dispose()


app = FastAPI(
    title="Empório da Música — API",
    description="Backend do assistente de atendimento da Empório da Música.",
    version="0.1.0",
    lifespan=lifespan,
)


app.include_router(health_router)
app.include_router(chat_router)
app = mount_chat_ui(app)
