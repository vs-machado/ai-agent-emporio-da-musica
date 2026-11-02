"""Simulações comerciais calculadas a partir do catálogo consultado."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Category, Product
from app.schemas.commerce import Cart, PaymentQuote, ShippingQuote
from app.schemas.web import PostalCodeLookup
from app.services.catalog import active_promotions, current_price, money, product_data
from app.services.postal import PostalCodeService


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
    def __init__(
        self, sessions: sessionmaker[Session], postal_codes: PostalCodeService | None = None
    ):
        self.sessions = sessions
        self.postal_codes = postal_codes if postal_codes is not None else PostalCodeService()

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
        """Consulta carrinho/destino e orienta a próxima etapa do atendimento.

        Args:
            request: Carrinho e destino por cidade/UF ou CEP, sem exigir classificação
                geográfica do cliente. Cobertura incerta é verificada com um funcionário.

        Returns:
            Contexto comercial sem tarifas, limites, modalidades ou prazos fixos.
            Condições locais são recuperadas por search_policy; fora da região
            metropolitana, a orientação é verificar o valor com um funcionário.
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
        }
        if request.destination_city:
            result["destination"] = {
                "city": request.destination_city,
                "state": request.destination_state,
            }
            result["destination_source"] = "customer"
        if request.destination_postal_code:
            lookup = self.postal_codes.lookup_postal_code(
                PostalCodeLookup(postal_code=request.destination_postal_code)
            )
            if lookup["status"] != "ok":
                return {**result, **lookup}
            address = lookup["address"]
            result["destination"] = address
            result["destination_source"] = lookup["source"]
        if "destination" not in result:
            return {
                **result,
                "status": "requires_information",
                "message": (
                    "Peça somente cidade e estado se o destino ainda não tiver sido informado. "
                    "Se já souber o destino, verifique o frete com um funcionário. "
                    "Não peça ao cliente que classifique a região de entrega."
                ),
            }
        address = result["destination"]
        if not (address["city"].casefold() == "campo grande" and address["state"] == "MS"):
            return {
                **result,
                "status": "requires_quote",
                "staff_quote_required": True,
                "staff_contacted": False,
                "follow_up_scheduled": False,
                "message": (
                    "Vou verificar com um funcionário quanto fica o frete para o seu destino "
                    "e te informar depois."
                ),
            }
        return {
            **result,
            "status": "requires_policy",
            "policy_lookup_required": True,
            "message": (
                "Consulte search_policy para as condições de frete da região metropolitana "
                "de Campo Grande no manual vigente. Use o subtotal consultado como contexto, "
                "sem assumir taxas, limites de gratuidade, modalidades ou prazos antigos. "
                "Se a política não esclarecer o caso, verifique o valor com um funcionário."
            ),
        }
