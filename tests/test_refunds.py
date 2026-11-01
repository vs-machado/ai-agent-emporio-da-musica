"""Referências de reembolso com dados históricos e sem operações financeiras."""

from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ingestion.loader import ingest
from app.models import Customer, Order, OrderItem, Product
from app.schemas.orders import RefundQuote
from app.tools import build_tools


@pytest.mark.parametrize(
    "selection",
    [
        [],
        [{"product_id": 130, "quantity": 0}],
        [{"product_id": 130, "quantity": -1}],
        [{"product_id": 130, "quantity": 1.5}],
        [{"product_id": 130, "quantity": "1"}],
        [{"product_id": 130, "quantity": True}],
        [{"product_id": 130}],
        [{"product_id": 130, "quantity": 1, "price_brl": "0.01"}],
        [{"product_id": 130, "quantity": 1}, {"product_id": 130, "quantity": 1}],
    ],
)
def test_refund_rejects_invalid_or_ambiguous_selection(selection):
    with pytest.raises(ValidationError):
        RefundQuote(order_id=3, email="cliente@example.com", items=selection)


def test_refund_does_not_accept_model_supplied_amount():
    with pytest.raises(ValidationError):
        RefundQuote(order_id=3, email="cliente@example.com", refund_amount_brl="1.00")


@pytest.fixture
def refund_context(engine):
    ingest(engine)
    tool = next(t for t in build_tools(sessionmaker(engine)) if t.name == "calculate_refund")

    def request(order_id, **kwargs):
        with Session(engine) as session:
            email = session.scalar(
                select(Customer.email).join(Order).where(Order.order_id == order_id)
            )
        return {"order_id": order_id, "email": email, **kwargs}

    return tool, request


@pytest.mark.parametrize("order_id,expected", [(1, "11499.00"), (3, "3450.00"), (15, "8348.00")])
def test_full_refund_uses_recorded_total_for_single_and_mixed_products(
    refund_context, order_id, expected
):
    tool, request = refund_context
    result = tool.invoke(request(order_id))
    assert result["status"] == "requires_confirmation"
    assert result["refund_amount_brl"] == expected
    assert result["refund_scope"] == "full"
    assert result["amount_kind"] == "recorded_total_reference"
    assert result["calculation_basis"] == "recorded_order_total"
    assert not result["payment_verified"]
    assert not result["eligibility_checked"]
    assert not result["refund_approved"]
    assert not result["refund_executed"]


def test_partial_refund_of_identical_products_does_not_use_current_prices(refund_context, engine):
    tool, request = refund_context
    with Session(engine) as session, session.begin():
        product = session.get(Product, 130)
        product.price_brl = Decimal("9999.00")
        product.stock_quantity = 0
        product.status = "discontinued"
    result = tool.invoke(request(3, items=[{"product_id": 130, "quantity": 1}]))
    assert result["refund_amount_brl"] == "1725.00"
    assert result["status"] == "requires_confirmation"
    assert result["amount_kind"] == "proportional_estimate"
    assert result["purchased_quantity"] == 2
    assert result["return_quantity"] == 1
    assert "convenção do protótipo" in result["message"]


def test_selecting_all_items_is_full_refund_even_with_multiple_products(refund_context):
    tool, request = refund_context
    result = tool.invoke(
        request(15, items=[{"product_id": 106, "quantity": 1}, {"product_id": 82, "quantity": 1}])
    )
    assert result["refund_scope"] == "full"
    assert result["refund_amount_brl"] == "8348.00"
    assert result["amount_kind"] == "recorded_total_reference"


def test_partial_refund_of_different_products_does_not_guess_allocation(refund_context):
    tool, request = refund_context
    result = tool.invoke(request(15, items=[{"product_id": 82, "quantity": 1}]))
    assert result["status"] == "requires_confirmation"
    assert result["refund_scope"] == "partial"
    assert result["refund_amount_brl"] is None
    assert result["calculation_basis"] == "historical_item_prices_unavailable"


@pytest.mark.parametrize("order_id", [17, 20])
def test_cancelled_order_requires_payment_verification_instead_of_zero_refund(
    refund_context, order_id
):
    tool, request = refund_context
    result = tool.invoke(request(order_id))
    assert result["status"] == "requires_confirmation"
    assert result["order_status"] == "cancelled"
    assert result["refund_amount_brl"] is None
    assert result["calculation_basis"] == "payment_verification_required"
    assert result["payment_verified"] is False
    assert "não comprova ausência de pagamento" in result["message"]


@pytest.mark.parametrize(
    "item",
    [
        {"product_id": 130, "quantity": 3},
        {"product_id": 81, "quantity": 1},
        {"product_id": 999999, "quantity": 1},
    ],
)
def test_refund_selection_must_belong_to_verified_order(refund_context, item):
    tool, request = refund_context
    result = tool.invoke(request(3, items=[item]))
    assert result["status"] == "invalid_input"
    assert "refund_amount_brl" not in result


def test_refund_failure_does_not_distinguish_wrong_email_and_missing_order(refund_context):
    tool, request = refund_context
    wrong = tool.invoke({**request(3), "email": "other@example.com"})
    missing = tool.invoke({"order_id": 999999, "email": "other@example.com"})
    assert wrong == missing
    assert wrong["status"] == "not_found"
    assert "order_total_brl" not in wrong


def test_proportional_refund_rounds_only_final_amount_and_never_mutates_order(
    refund_context, engine
):
    tool, request = refund_context
    with Session(engine) as session, session.begin():
        session.get(Order, 3).total_brl = Decimal("100.00")
        session.get(OrderItem, (3, 130)).quantity = 3
    args = request(3, items=[{"product_id": 130, "quantity": 2}])
    result = tool.invoke(args)
    assert result["refund_amount_brl"] == "66.67"
    assert tool.invoke(args) == result
    with Session(engine) as session:
        assert session.get(Order, 3).total_brl == Decimal("100.00")
        assert session.get(Order, 3).status == "delivered"
        assert session.get(OrderItem, (3, 130)).quantity == 3
        assert session.scalar(select(func.count()).select_from(Order)) == 20


def test_refund_with_missing_order_items_is_not_simulated(refund_context, engine):
    tool, request = refund_context
    with Session(engine) as session, session.begin():
        session.delete(session.get(OrderItem, (3, 130)))
    result = tool.invoke(request(3))
    assert result["refund_amount_brl"] is None
    assert result["calculation_basis"] == "order_items_unavailable"
