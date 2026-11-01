"""Ponto de entrada da API HTTP."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings
from app.database import create_database_engine, create_session_factory
from app.routes.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    engine = create_database_engine(settings)
    app.state.session_factory = create_session_factory(engine)
    try:
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
