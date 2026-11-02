"""ViaCEP e orientação de frete com transporte injetado, sem acesso à internet."""

import json

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from app.ingestion.loader import ingest
from app.schemas.web import PostalCodeLookup
from app.services.errors import ExternalLookupError
from app.services.postal import PostalCodeService, ViaCEPClient
from app.tools import build_tools


@pytest.mark.parametrize("value", ["01001000", "01001-000", " 01001-000 "])
def test_postal_code_accepts_only_supported_format(value):
    assert PostalCodeLookup(postal_code=value).postal_code == "01001000"


@pytest.mark.parametrize(
    "value", ["123", "01001 000", "0100100A", "0100-1000", 1001000, "http://local"]
)
def test_invalid_postal_code_is_rejected(value):
    with pytest.raises(ValidationError):
        PostalCodeLookup(postal_code=value)


def test_viacep_uses_normalized_code_and_returns_structured_address():
    def respond(request):
        assert request.method == "GET"
        assert str(request.url) == "https://viacep.com.br/ws/01001000/json/"
        return httpx.Response(
            200,
            json={
                "cep": "01001-000",
                "localidade": "São Paulo",
                "uf": "SP",
                "logradouro": "Praça da Sé",
                "bairro": "Sé",
                "ibge": "3550308",
            },
        )

    service = PostalCodeService(ViaCEPClient(httpx.MockTransport(respond)))
    result = service.lookup_postal_code(PostalCodeLookup(postal_code="01001-000"))
    assert result["status"] == "ok"
    assert result["source"] == "ViaCEP"
    assert result["address"] == {
        "postal_code": "01001000",
        "city": "São Paulo",
        "state": "SP",
        "street": "Praça da Sé",
        "neighborhood": "Sé",
        "ibge_code": "3550308",
    }
    assert "frete" in result["message"]


@pytest.mark.parametrize("error", [True, "true"])
def test_viacep_does_not_invent_missing_address(error):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"erro": error}))
    service = PostalCodeService(ViaCEPClient(transport))
    assert (
        service.lookup_postal_code(PostalCodeLookup(postal_code="99999999"))["status"]
        == "not_found"
    )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, text="private upstream error"),
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"cep": "79002000", "localidade": "Campo Grande", "uf": "MS"}),
        httpx.Response(200, json={"cep": "01001-000", "uf": "SP"}),
        httpx.Response(302, headers={"location": "http://private"}),
    ],
)
def test_viacep_rejects_invalid_transport_or_address(response):
    provider = ViaCEPClient(httpx.MockTransport(lambda request: response))
    with pytest.raises(ExternalLookupError):
        provider.lookup("01001000")


def test_external_failure_is_sanitized_at_tool_boundary():
    class FailingProvider:
        def lookup(self, postal_code):
            raise ExternalLookupError("private error")

    tools = {t.name: t for t in build_tools(sessionmaker(), postal_code_provider=FailingProvider())}
    result = tools["lookup_postal_code"].invoke({"postal_code": "01001-000"})
    assert result["status"] == "unavailable"
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(
    ("city", "state", "region", "status", "shipping"),
    [
        ("Campo Grande", "MS", "unknown", "requires_policy", None),
        ("São Paulo", "SP", "unknown", "requires_quote", None),
        ("Campo Grande", "AL", "unknown", "requires_quote", None),
        ("Outra Cidade", "MS", "unknown", "requires_information", None),
        ("São Paulo", "SP", "campo_grande_metropolitan", "requires_confirmation", None),
    ],
)
def test_shipping_uses_cep_without_inventing_metropolitan_coverage(
    engine, city, state, region, status, shipping
):
    ingest(engine)

    class Provider:
        def lookup(self, postal_code):
            return {"postal_code": postal_code, "city": city, "state": state}

    tool = next(
        t
        for t in build_tools(sessionmaker(engine), postal_code_provider=Provider())
        if t.name == "calculate_shipping"
    )
    result = tool.invoke(
        {
            "items": [{"product_id": 83}],
            "destination_postal_code": "79002-000",
            "destination_region": region,
        }
    )
    assert result["status"] == status
    assert result["shipping_brl"] == shipping
    assert result["destination"]["city"] == city


def test_shipping_does_not_calculate_for_unknown_postal_code(engine):
    ingest(engine)

    class Provider:
        def lookup(self, postal_code):
            return None

    tool = next(
        t
        for t in build_tools(sessionmaker(engine), postal_code_provider=Provider())
        if t.name == "calculate_shipping"
    )
    result = tool.invoke(
        {
            "items": [{"product_id": 83}],
            "destination_postal_code": "99999999",
            "destination_region": "campo_grande_metropolitan",
        }
    )
    assert result["status"] == "not_found"
    assert result["shipping_brl"] is None
