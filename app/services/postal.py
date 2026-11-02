"""Consulta gratuita de CEP com endereço estruturado e sem inferir cobertura de entrega."""

from typing import Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.schemas.base import PostalCode
from app.schemas.web import PostalCodeLookup
from app.services.errors import ExternalLookupError


class PostalCodeProvider(Protocol):
    def lookup(self, postal_code: str) -> dict | None: ...


class ViaCEPAddress(BaseModel):
    cep: PostalCode
    localidade: str = Field(min_length=1, max_length=200)
    uf: str = Field(pattern=r"^[A-Z]{2}$")
    logradouro: str = Field(default="", max_length=300)
    bairro: str = Field(default="", max_length=200)
    ibge: str = Field(default="", pattern=r"^([0-9]{7})?$")


class ViaCEPClient:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def lookup(self, postal_code: str) -> dict | None:
        try:
            with httpx.Client(
                transport=self.transport, timeout=10, follow_redirects=False
            ) as client:
                response = client.get(f"https://viacep.com.br/ws/{postal_code}/json/")
                response.raise_for_status()
                data = response.json()
            if not isinstance(data, dict):
                raise ExternalLookupError("Resposta inválida do serviço de CEP")
            if data.get("erro") in (True, "true"):
                return None
            address = ViaCEPAddress.model_validate(data)
            if address.cep != postal_code:
                raise ExternalLookupError("Resposta de CEP divergente")
            return {
                "postal_code": address.cep,
                "city": address.localidade,
                "state": address.uf,
                "street": address.logradouro,
                "neighborhood": address.bairro,
                "ibge_code": address.ibge,
            }
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise ExternalLookupError("Consulta de CEP indisponível") from exc


class PostalCodeService:
    def __init__(self, provider: PostalCodeProvider | None = None):
        self.provider = provider if provider is not None else ViaCEPClient()

    def lookup_postal_code(self, request: PostalCodeLookup) -> dict:
        address = self.provider.lookup(request.postal_code)
        if address is None:
            return {"status": "not_found", "message": "CEP não encontrado. Confira com o cliente."}
        return {
            "status": "ok",
            "address": address,
            "source": "ViaCEP",
            "source_url": f"https://viacep.com.br/ws/{request.postal_code}/json/",
            "message": "O endereço não confirma, sozinho, cobertura de entrega ou valor de frete.",
        }
