"""Contrato de consulta externa de CEP."""

from pydantic import Field

from app.schemas.base import Input, PostalCode


class PostalCodeLookup(Input):
    postal_code: PostalCode = Field(description="CEP informado pelo cliente, com ou sem hífen")
