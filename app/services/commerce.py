"""Simulações comerciais calculadas a partir do catálogo consultado."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Category, Product
from app.schemas.commerce import Cart, PaymentQuote, ShippingQuote
from app.services.catalog import active_promotions, current_price, money, product_data


def quote_cart(session: Session, request: Cart) -> dict:
    """Consulta preços e estoque sem alterar o catálogo ou criar reservas.

    Args:
        session: Sessão utilizada para consultar produtos e promoções.
        request: Produtos e quantidades validados, sem valores fornecidos pelo modelo.

    Returns:
        Linhas e totais atuais, com PIX apenas nos itens sem promoção. Produtos
        ausentes ou sem estoque suficiente interrompem a simulação.
    """
    ids = [item.product_id for item in request.items]
    products = {
        p.product_id: (p, category)
        for p, category in session.execute(
            select(Product, Category.name).join(Category).where(Product.product_id.in_(ids))
        )
    }
    if len(products) != len(ids):
        return {"status": "not_found", "message": "Um ou mais produtos não foram encontrados."}
    promotions = active_promotions(session, ids)
    lines = []
    subtotal = Decimal(0)
    pix_total = Decimal(0)
    for item in request.items:
        product, category = products[item.product_id]
        if product.status != "active" or product.stock_quantity < item.quantity:
            return {
                "status": "unavailable_product",
                "product_id": item.product_id,
                "message": (
                    "Produto indisponível ou quantidade maior que o estoque; consulte alternativas."
                ),
            }
        promotion = promotions.get(item.product_id)
        price = current_price(product, promotion)
        total = price * item.quantity
        pix_line_total = Decimal(money(total if promotion else total * Decimal("0.95")))
        subtotal += total
        pix_total += pix_line_total
        lines.append(
            {
                **product_data(product, category, promotion),
                "quantity": item.quantity,
                "line_total_brl": money(total),
                "pix_line_total_brl": money(pix_line_total),
            }
        )
    return {
        "status": "ok",
        "items": lines,
        "subtotal_brl": money(subtotal),
        "pix_total_brl": money(pix_total),
    }


def installment_schedule(total: Decimal, count: int) -> list[str]:
    """Distribui centavos excedentes nas primeiras parcelas, preservando o total."""
    cents = int(total * 100)
    base, extra = divmod(cents, count)
    return [money(Decimal(base + (index < extra)) / 100) for index in range(count)]


class CommerceService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def calculate_payment(self, request: PaymentQuote) -> dict:
        """Simula pagamento sem aceitar preços ou descontos fornecidos pelo modelo.

        Args:
            request: Carrinho, forma de pagamento e número de parcelas.

        Returns:
            Valores em reais e condição calculada. As parcelas distribuem os
            centavos sem alterar o total. Condições ambíguas exigem confirmação.
            Não efetua pagamento nem reserva produtos.
        """
        with self.sessions() as session:
            cart = quote_cart(session, request)
        if cart["status"] != "ok":
            return cart
        total = Decimal(cart["subtotal_brl"])
        result = {
            **cart,
            "payment_method": request.payment_method,
            "total_brl": cart["pix_total_brl"] if request.payment_method == "pix" else money(total),
            "shipping_included": False,
            "policy_source": "Manual, seções 3, 3.1 e 6.2",
        }
        if request.payment_method == "combined":
            return {
                **result,
                "status": "requires_confirmation" if total > 2000 else "not_allowed",
                "combined_payment_eligible": total > 2000,
                "message": (
                    "Combinação somente acima de R$ 2.000,00; distribuição e descontos "
                    "precisam ser confirmados pela equipe."
                ),
            }
        if request.payment_method == "credit":
            schedule = installment_schedule(total, request.installments)
            smallest = min(map(Decimal, schedule))
            result["installments_brl"] = schedule
            result["interest_percent"] = "0.00"
            if request.installments > 1:
                if request.installments <= 3 and smallest < 50:
                    result.update(
                        status="requires_confirmation",
                        message=(
                            "O manual não esclarece a exceção abaixo de R$ 50,00 em até 3x. "
                            "Confirmar com a equipe antes de oferecer essa condição."
                        ),
                    )
                elif (
                    request.installments <= 6 and request.installments >= 4 and smallest < 80
                ) or (request.installments >= 7 and smallest < 100):
                    result.update(
                        status="not_allowed",
                        message=(
                            "Parcelas abaixo do mínimo: 4 a 6x exigem R$ 80,00; "
                            "7 a 12x exigem R$ 100,00."
                        ),
                    )
        elif request.payment_method == "boleto":
            result["settlement_business_days_max"] = 3
        elif request.payment_method == "debit":
            result["accepted_brands"] = "Todas as bandeiras"
        result["pix_rule"] = "5% somente nos itens sem promoção; descontos não cumulativos."
        return result

    def calculate_shipping(self, request: ShippingQuote) -> dict:
        """Estima frete local a partir do subtotal atual, sem desconto de pagamento.

        Args:
            request: Carrinho e região confirmada pelo cliente; não infere a
                região metropolitana a partir de CEP ou nome de outra cidade.

        Returns:
            Frete e prazo em dias úteis quando definidos pela política. Para
            outras regiões, retorna modalidades e necessidade de cotação, pois
            não há integração com transportadoras, pesos ou dimensões na fonte.
        """
        with self.sessions() as session:
            cart = quote_cart(session, request)
        if cart["status"] != "ok":
            return cart
        result = {
            "status": "ok",
            "subtotal_brl": cart["subtotal_brl"],
            "shipping_brl": None,
            "price_basis": "Subtotal com promoções, antes de desconto por forma de pagamento",
            "policy_source": "Manual, seções 5.1 e 5.2",
        }
        if request.destination_region == "unknown":
            return {
                **result,
                "status": "requires_information",
                "message": "Confirme se o destino está na região metropolitana de Campo Grande.",
            }
        if request.destination_region == "other":
            return {
                **result,
                "status": "requires_quote",
                "methods": [
                    {"name": "PAC", "business_days": [5, 12]},
                    {"name": "SEDEX", "business_days": [2, 5]},
                    {"name": "Jadlog", "business_days": [3, 8]},
                ],
                "message": (
                    "Cotação depende de CEP, peso e dimensões. Instrumentos de grande porte "
                    "podem exigir cotação individual; não há integração com transportadoras "
                    "neste protótipo."
                ),
            }
        total = Decimal(cart["subtotal_brl"])
        if total == 500:
            return {
                **result,
                "status": "requires_confirmation",
                "message": (
                    "O manual não define frete para exatamente R$ 500,00. Confirmar com a equipe."
                ),
            }
        return {
            **result,
            "shipping_brl": "0.00" if total > 500 else "35.00",
            "method": "Motoboy próprio",
            "business_days": [1, 3],
            "message": "Prazo estimado; haverá contato telefônico antes da entrega.",
        }
