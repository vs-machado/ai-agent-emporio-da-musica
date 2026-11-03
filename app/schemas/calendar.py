"""Contrato da consulta local de feriados nacionais e de Mato Grosso do Sul."""

from datetime import date

from pydantic import Field, field_validator

from app.schemas.base import Input


class HolidayLookup(Input):
    target_date: date = Field(
        ge=date(1996, 1, 1),
        le=date(2100, 12, 31),
        description=(
            "Data a consultar em YYYY-MM-DD, entre 1996 e 2100. Para hoje, obtenha a data "
            "local por get_current_datetime. País BR e estado MS são fixos."
        ),
    )

    @field_validator("target_date", mode="before")
    @classmethod
    def validate_date_format(cls, value):
        if type(value) is date:
            return value
        if isinstance(value, str):
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        raise ValueError("Informe uma data em YYYY-MM-DD, não um timestamp")
