"""Configuração e tipos comuns dos contratos de atendimento."""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def normalize_postal_code(value):
    if isinstance(value, str):
        value = value.strip()
        if len(value) == 9 and value[5] == "-":
            return value[:5] + value[6:]
    return value


Identifier = Annotated[int, Field(strict=True, gt=0, le=2_147_483_647)]
Text = Annotated[str, Field(min_length=1, max_length=200)]
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Email = Annotated[str, Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")]
PostalCode = Annotated[
    str,
    BeforeValidator(normalize_postal_code),
    Field(strict=True, pattern=r"^[0-9]{8}$"),
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
