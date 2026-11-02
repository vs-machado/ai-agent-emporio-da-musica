"""Regras de atendimento exercitadas pelas tools contra PostgreSQL real."""

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.ingestion.loader import ingest
from app.models import Complaint, Product
from app.tools import build_tools


@pytest.fixture
def tools(engine):
    ingest(engine)
    return {tool.name: tool for tool in build_tools(sessionmaker(bind=engine))}


def test_catalog_filters_accents_brand_current_price_and_pagination(tools):
    response = tools["search_products"].invoke(
        {"category": "Violoes", "brand": "yamaha", "max_price": "1000"}
    )
    assert response["products"]
    assert all(
        p["category_id"] == 5 and "yamaha" in p["name"].lower() for p in response["products"]
    )
    assert all(
        Decimal(p["current_price_brl"]) <= 1000 and p["available"] for p in response["products"]
    )
    first = tools["search_products"].invoke({"limit": 1})
    second = tools["search_products"].invoke({"limit": 1, "offset": 1})
    assert first["has_more"] is True
    assert first["products"][0]["product_id"] != second["products"][0]["product_id"]
    literal_match = tools["search_products"].invoke({"query": "%"})["products"]
    assert [p["product_id"] for p in literal_match] == [140]
    promo = tools["search_products"].invoke({"query": "Kalani KAL-700T", "max_price": "330"})
    assert promo["products"][0]["current_price_brl"] == "323.10"


@pytest.mark.parametrize("category", ["violão", "violao", "Violões", "violoes"])
def test_catalog_accepts_singular_and_plural_category_names(tools, category):
    result = tools["search_products"].invoke({"category": category, "limit": 20})
    assert len(result["products"]) == 20
    assert result["has_more"]
    assert all(p["category_id"] == 5 and p["available"] for p in result["products"])


@pytest.mark.parametrize(
    ("query", "material"),
    [("violão de nylon", "nylon"), ("violões de náilon", "nylon"), ("violão de aço", "steel")],
)
def test_catalog_searches_guitar_material_without_false_stock_absence(
    tools, engine, query, material
):
    result = tools["search_products"].invoke({"category": "violão", "query": query, "limit": 20})
    with Session(engine) as session:
        expected = {
            p.product_id
            for p in session.scalars(
                select(Product).where(
                    Product.category_id == 5, Product.status == "active", Product.stock_quantity > 0
                )
            )
            if p.specs.get("strings") == material
        }
    assert {p["product_id"] for p in result["products"]} == expected
    assert all(p["specs"]["strings"] == material for p in result["products"])
    prices = [Decimal(p["current_price_brl"]) for p in result["products"]]
    assert prices == sorted(prices)


def test_empty_catalog_search_does_not_claim_category_has_no_stock(tools):
    result = tools["search_products"].invoke({"category": "violão", "brand": "marca-inexistente"})
    assert result["products"] == []
    assert "não confirma" in result["search_guidance"]
    assert tools["search_products"].invoke({"category": "violão"})["products"]


@pytest.mark.parametrize("product_id", [96, 113, 130, 136])
def test_unavailable_products_have_available_alternatives(tools, product_id):
    response = tools["get_product_details"].invoke({"product_id": product_id})
    assert response["product"]["available"] is False
    assert response["alternatives"]
    assert all(
        p["available"] and p["category_id"] == response["product"]["category_id"]
        for p in response["alternatives"]
    )
    assert tools["get_product_details"].invoke({"product_id": 99999})["status"] == "not_found"


def test_inactive_campaigns_do_not_offer_expired_discount(tools):
    assert tools["get_active_promotions"].invoke({"campaign": "Black Friday"})["promotions"] == []
    response = tools["get_active_promotions"].invoke(
        {"campaign": "Black Friday", "include_inactive": True}
    )
    assert response["promotions"]
    assert all(
        not p["is_active"] and p["campaign_price_brl"] is None for p in response["promotions"]
    )
    active = tools["get_active_promotions"].invoke({"product_id": 127})["promotions"][0]
    assert active["campaign_price_brl"] == active["product"]["current_price_brl"] == "323.10"


def test_pix_is_only_applied_to_nonpromoted_items(tools):
    response = tools["calculate_payment"].invoke(
        {"items": [{"product_id": 81}, {"product_id": 127}], "payment_method": "pix"}
    )
    assert response["total_brl"] == "893.01"
    assert response["subtotal_brl"] == "923.00"
    result = tools["calculate_payment"].invoke(
        {"items": [{"product_id": 127}], "payment_method": "pix"}
    )
    assert result["total_brl"] == "323.10"
    assert result["items"][0]["promotion_id"] == 12
    search = tools["search_products"].invoke({"query": "Kalani KAL-700T", "max_price": "330"})
    assert search["products"][0]["current_price_brl"] == result["total_brl"]


@pytest.mark.parametrize(
    ("total", "count", "status"),
    [
        ("320.00", 4, "ok"),
        ("319.99", 4, "not_allowed"),
        ("480.00", 6, "ok"),
        ("479.99", 6, "not_allowed"),
        ("700.00", 7, "ok"),
        ("699.99", 7, "not_allowed"),
        ("1200.00", 12, "ok"),
        ("1199.99", 12, "not_allowed"),
        ("99.00", 2, "requires_confirmation"),
        ("150.00", 3, "ok"),
        ("49.99", 1, "ok"),
    ],
)
def test_credit_limits_and_cent_distribution(tools, engine, total, count, status):
    with Session(engine) as session, session.begin():
        session.get(Product, 81).price_brl = Decimal(total)
    result = tools["calculate_payment"].invoke(
        {"items": [{"product_id": 81}], "payment_method": "credit", "installments": count}
    )
    assert result["status"] == status
    assert sum(map(Decimal, result["installments_brl"])) == Decimal(total)
    assert len(result["installments_brl"]) == count


@pytest.mark.parametrize(("total", "eligible"), [("2000.00", False), ("2000.01", True)])
def test_combined_payment_requires_strict_threshold_and_confirmation(
    tools, engine, total, eligible
):
    with Session(engine) as session, session.begin():
        session.get(Product, 81).price_brl = Decimal(total)
    result = tools["calculate_payment"].invoke(
        {"items": [{"product_id": 81}], "payment_method": "combined"}
    )
    assert result["combined_payment_eligible"] is eligible
    assert result["status"] == ("requires_confirmation" if eligible else "not_allowed")


@pytest.mark.parametrize(
    ("total", "cost", "status"),
    [
        ("499.99", "35.00", "ok"),
        ("500.00", None, "requires_confirmation"),
        ("500.01", "0.00", "ok"),
    ],
)
def test_local_shipping_boundaries(tools, engine, total, cost, status):
    with Session(engine) as session, session.begin():
        session.get(Product, 81).price_brl = Decimal(total)
    result = tools["calculate_shipping"].invoke(
        {"items": [{"product_id": 81}], "destination_region": "campo_grande_metropolitan"}
    )
    assert result["status"] == status
    assert result["shipping_brl"] == cost


def test_shipping_requires_region_or_external_quote(tools):
    args = {"items": [{"product_id": 81}]}
    assert tools["calculate_shipping"].invoke(args)["status"] == "requires_information"
    result = tools["calculate_shipping"].invoke({**args, "destination_region": "other"})
    assert result["status"] == "requires_quote"
    assert result["shipping_brl"] is None
    assert {method["name"] for method in result["methods"]} == {"PAC", "SEDEX", "Jadlog"}


@pytest.mark.parametrize("product_id,quantity", [(96, 1), (130, 1), (136, 1), (81, 13)])
def test_quotes_reject_unavailable_or_insufficient_stock(tools, product_id, quantity):
    cart = {"items": [{"product_id": product_id, "quantity": quantity}]}
    assert (
        tools["calculate_payment"].invoke({**cart, "payment_method": "pix"})["status"]
        == "unavailable_product"
    )
    assert tools["calculate_shipping"].invoke(cart)["status"] == "unavailable_product"


def test_order_lookup_requires_matching_email_and_minimizes_output(tools):
    good = tools["get_order_status"].invoke({"order_id": 1, "email": "PEDRO.OLIVEIRA@JMAIL.COM"})
    assert good["status"] == "ok"
    assert good["order_status"] == "delivered"
    assert good["tracking_code"] == "BRAB1234567BR"
    assert good["actual_delivery_date"] is None
    assert "email" not in json.dumps(good) and "phone" not in json.dumps(good)
    wrong_email = tools["get_order_status"].invoke({"order_id": 1, "email": "other@example.com"})
    missing_order = tools["get_order_status"].invoke(
        {"order_id": 999, "email": "other@example.com"}
    )
    assert wrong_email == missing_order
    waiting = tools["get_order_status"].invoke(
        {"order_id": 9, "email": "juliana.almeida@hayoo.com.br"}
    )
    assert waiting["tracking_code"] is None
    assert waiting["estimated_delivery"] is None


def test_complaint_is_persisted_idempotent_and_not_forwarded(tools, engine):
    args = {
        "request_id": str(uuid4()),
        "contact_email": "pedro.oliveira@jmail.com",
        "message": "Quero registrar um problema no atendimento.",
        "order_id": 1,
    }
    first = tools["register_complaint"].invoke(args)
    assert first["status"] == "registered"
    assert first["complaint_status"] == "open"
    assert first["forwarded_to_team"] is False
    assert tools["register_complaint"].invoke(args)["complaint_id"] == first["complaint_id"]
    conflict = tools["register_complaint"].invoke(
        {**args, "message": "Outro relato não pode reutilizar a chave."}
    )
    assert conflict["status"] == "request_conflict"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Complaint)) == 1
        assert session.scalar(select(Complaint.message)) == args["message"]


def test_complaint_can_be_without_order_but_cannot_link_another_customer(tools, engine):
    args = {
        "request_id": str(uuid4()),
        "contact_email": "other@example.com",
        "message": "Gostaria de registrar uma reclamação geral.",
    }
    assert tools["register_complaint"].invoke({**args, "order_id": 1})["status"] == "not_found"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Complaint)) == 0
    assert tools["register_complaint"].invoke(args)["status"] == "registered"


def test_failed_complaint_does_not_return_protocol_or_persist(tools, engine):
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE complaints ADD CONSTRAINT test_write_failure CHECK (false)")
        )
    try:
        result = tools["register_complaint"].invoke(
            {
                "request_id": str(uuid4()),
                "contact_email": "other@example.com",
                "message": "Um relato que não deve ser persistido.",
            }
        )
        assert result["status"] == "unavailable"
        assert "complaint_id" not in result
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Complaint)) == 0
    finally:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE complaints DROP CONSTRAINT test_write_failure"))
