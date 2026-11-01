"""Contratos de consulta e evidências recuperadas do manual de políticas."""

from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from app.schemas.base import Input


class PolicySearch(Input):
    query: Annotated[str, Field(min_length=3, max_length=500)] = Field(
        description="Dúvida a consultar no manual de políticas"
    )
    limit: Annotated[int, Field(strict=True, ge=1, le=5)] = Field(
        default=5, description="Máximo de evidências finais após expansão e deduplicação"
    )


class PolicyPassage(BaseModel):
    text: str = Field(min_length=1)
    source: str = Field(min_length=1, max_length=200)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    section: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    document_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_pages(self):
        if self.page_end < self.page_start:
            raise ValueError("Página final anterior à inicial")
        return self
