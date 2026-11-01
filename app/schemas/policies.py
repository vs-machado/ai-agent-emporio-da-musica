"""Contratos de consulta e evidências recuperadas do manual de políticas."""

from typing import Annotated

from pydantic import BaseModel, Field

from app.schemas.base import Input


class PolicySearch(Input):
    query: Annotated[str, Field(min_length=3, max_length=500)] = Field(
        description="Dúvida a consultar no manual de políticas"
    )
    limit: Annotated[int, Field(strict=True, ge=1, le=5)] = Field(
        default=3, description="Máximo de trechos recuperados"
    )


class PolicyPassage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    source: str = Field(min_length=1, max_length=200)
    page: int = Field(ge=1)
    section: str = Field(min_length=1, max_length=200)
