"""Ponto de entrada da API HTTP."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings
from app.database import create_database_engine, create_session_factory
from app.rag.retrieval import LlamaIndexPolicyRetriever
from app.routes.health import router as health_router
from app.tools import build_tools


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    engine = create_database_engine(settings)
    app.state.session_factory = create_session_factory(engine)
    try:
        app.state.policy_retriever = LlamaIndexPolicyRetriever(engine, settings)
        app.state.tools = build_tools(app.state.session_factory, app.state.policy_retriever)
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
