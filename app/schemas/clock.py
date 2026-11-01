"""Contrato de consulta do relógio no fuso da loja."""

from app.schemas.base import Input


class CurrentDateTime(Input):
    """Consulta sem parâmetros; o cliente não define o relógio nem o fuso da loja."""
