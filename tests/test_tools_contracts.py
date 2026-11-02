"""Contratos das tools sem chamadas a banco ou provedores."""

import json
from uuid import uuid4

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.schemas.catalog import ProductDetails, ProductSearch
from app.schemas.commerce import PaymentQuote
from app.schemas.policies import PolicyPassage
from app.tools import build_tools
from app.tools.registry import service_tool


def test_tools_expose_schemas_for_tool_calling():
    tools = build_tools(sessionmaker())
    assert {tool.name for tool in tools} == {
        "get_current_datetime",
        "search_products",
        "get_product_details",
        "get_active_promotions",
        "get_order_status",
        "calculate_refund",
        "calculate_payment",
        "calculate_shipping",
        "lookup_postal_code",
        "register_complaint",
        "search_policy",
    }
    for tool in tools:
        schema = convert_to_openai_tool(tool)["function"]
        assert schema["description"]
        if tool.name == "get_current_datetime":
            assert schema["parameters"]["properties"] == {}
        else:
            assert schema["parameters"]["properties"]
        input_schema = (
            tool.args_schema
            if isinstance(tool.args_schema, dict)
            else tool.args_schema.model_json_schema()
        )
        assert input_schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"min_price": "100", "max_price": "99"},
        {"limit": 21},
        {"offset": -1},
        {"query": "   "},
        {"unexpected": "value"},
    ],
)
def test_search_rejects_invalid_filters(payload):
    with pytest.raises(ValidationError):
        ProductSearch.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"items": []},
        {"items": [{"product_id": 81}, {"product_id": 81}]},
        {"items": [{"product_id": 81, "quantity": 0}]},
        {"items": [{"product_id": 81, "price_brl": "0.01"}]},
        {"items": [{"product_id": 81}], "installments": 13},
        {"items": [{"product_id": 81}], "payment_method": "pix", "installments": 2},
    ],
)
def test_payment_rejects_invalid_cart(payload):
    with pytest.raises(ValidationError):
        PaymentQuote.model_validate({"payment_method": "credit", **payload})


def test_invalid_tool_arguments_do_not_expose_input_or_access_database():
    tool = next(t for t in build_tools(sessionmaker()) if t.name == "get_order_status")
    response = tool.invoke({"order_id": 1, "email": "invalid-private-value"})
    assert "Parâmetros inválidos" in response
    assert "invalid-private-value" not in response


def test_database_failure_is_sanitized():
    def failing(_request):
        raise OperationalError("private SQL", {"password": "private-secret"}, Exception("failure"))

    tool = service_tool("failure", "Consulta de teste", ProductDetails, failing)
    response = tool.invoke({"product_id": 81})
    assert response["status"] == "unavailable"
    assert "private" not in json.dumps(response)


def test_unconfigured_policy_retriever_returns_unavailable_as_tool_message():
    tool = next(t for t in build_tools(sessionmaker()) if t.name == "search_policy")
    response = tool.invoke(
        {
            "type": "tool_call",
            "name": "search_policy",
            "id": str(uuid4()),
            "args": {"query": "Qual a garantia?"},
        }
    )
    assert json.loads(response.content)["status"] == "unavailable"


def test_policy_retriever_returns_only_bounded_evidence_with_metadata():
    class Retriever:
        def search(self, query, limit):
            assert query == "garantia"
            assert limit == 1
            return [
                PolicyPassage(
                    text="Trecho de teste",
                    source="manual.pdf",
                    page_start=7,
                    page_end=7,
                    section="8.1",
                    title="Garantia legal",
                    document_version="2.1",
                )
            ] * 2

    tool = next(t for t in build_tools(sessionmaker(), Retriever()) if t.name == "search_policy")
    result = tool.invoke({"query": "garantia", "limit": 1})
    assert result["status"] == "ok"
    assert len(result["passages"]) == 1
    assert result["passages"][0]["page_start"] == 7


def test_empty_policy_search_does_not_fabricate_rules():
    class Retriever:
        def search(self, query, limit):
            return []

    tool = next(t for t in build_tools(sessionmaker(), Retriever()) if t.name == "search_policy")
    assert tool.invoke({"query": "garantia"})["status"] == "not_found"


def test_policy_transport_failure_returns_unavailable():
    class Retriever:
        def search(self, query, limit):
            raise TimeoutError("private endpoint")

    tool = next(t for t in build_tools(sessionmaker(), Retriever()) if t.name == "search_policy")
    assert tool.invoke({"query": "garantia"})["status"] == "unavailable"
