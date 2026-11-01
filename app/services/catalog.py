"""Consultas ao catálogo e seleção não cumulativa de promoções."""

import unicodedata
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Category, Product, Promotion
from app.schemas.catalog import ProductDetails, ProductSearch, PromotionSearch


def money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def normalized(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def contains_text(column, value: str):
    return func.translate(
        func.lower(column), "áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc"
    ).contains(normalized(value), autoescape=True)


def active_promotions(session: Session, product_ids: list[int]) -> dict[int, Promotion]:
    """Consulta promoções, pressupondo uma única ativa por produto nos dados atuais."""
    promotions = session.scalars(
        select(Promotion)
        .where(Promotion.product_id.in_(product_ids), Promotion.is_active.is_(True))
        .order_by(Promotion.promotion_id)
    )
    result = {}
    for promotion in promotions:
        result.setdefault(promotion.product_id, promotion)
    return result


def current_price(product: Product, promotion: Promotion | None) -> Decimal:
    discount = promotion.discount_percent if promotion else Decimal(0)
    return (product.price_brl * (1 - discount / 100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def product_data(product: Product, category: str, promotion: Promotion | None) -> dict:
    return {
        "product_id": product.product_id,
        "name": product.name,
        "category": category,
        "category_id": product.category_id,
        "original_price_brl": money(product.price_brl),
        "current_price_brl": money(current_price(product, promotion)),
        "promotion_id": promotion.promotion_id if promotion else None,
        "discount_percent": money(promotion.discount_percent) if promotion else "0.00",
        "status": product.status,
        "stock_quantity": product.stock_quantity,
        "available": product.status == "active" and product.stock_quantity > 0,
    }


class CatalogService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def search_products(self, request: ProductSearch) -> dict:
        """Pesquisa por termos e preço atual, incluindo promoções não cumulativas.

        Args:
            request: Filtros de catálogo. Marca é buscada no nome, pois não há
                coluna própria. A busca textual ignora caixa e acentos latinos.

        Returns:
            Página ordenada por preço atual e ID, com disponibilidade calculada.
        """
        active_discount = (
            select(Promotion.discount_percent)
            .where(Promotion.product_id == Product.product_id, Promotion.is_active.is_(True))
            .order_by(Promotion.promotion_id)
            .limit(1)
            .correlate(Product)
            .scalar_subquery()
        )
        price = func.round(Product.price_brl * (1 - func.coalesce(active_discount, 0) / 100), 2)
        statement = select(Product, Category.name).join(Category)
        if request.query:
            for term in request.query.split():
                statement = statement.where(
                    contains_text(Product.name, term)
                    | contains_text(Product.description, term)
                    | contains_text(Category.name, term)
                )
        if request.category:
            statement = statement.where(contains_text(Category.name, request.category))
        if request.brand:
            statement = statement.where(contains_text(Product.name, request.brand))
        if request.available_only:
            statement = statement.where(Product.status == "active", Product.stock_quantity > 0)
        if request.min_price is not None:
            statement = statement.where(price >= request.min_price)
        if request.max_price is not None:
            statement = statement.where(price <= request.max_price)
        statement = (
            statement.order_by(price, Product.product_id)
            .offset(request.offset)
            .limit(request.limit + 1)
        )
        with self.sessions() as session:
            rows = session.execute(statement).all()
            promotions = active_promotions(session, [product.product_id for product, _ in rows])
            return {
                "status": "ok",
                "products": [
                    product_data(product, category, promotions.get(product.product_id))
                    for product, category in rows[: request.limit]
                ],
                "has_more": len(rows) > request.limit,
                "brand_filter_source": "product_name",
                "price_basis": "current_price_without_pix",
            }

    def get_product_details(self, request: ProductDetails) -> dict:
        """Consulta um produto e sugere alternativas da mesma categoria.

        Args:
            request: ID do produto, inclusive inativo ou sem estoque.

        Returns:
            Detalhes e alternativas disponíveis quando necessário. As sugestões
            não comprovam equivalência técnica nem sucessão de modelos.
        """
        with self.sessions() as session:
            row = session.execute(
                select(Product, Category.name)
                .join(Category)
                .where(Product.product_id == request.product_id)
            ).first()
            if row is None:
                return {"status": "not_found", "message": "Produto não encontrado no catálogo."}
            product, category = row
            alternatives = []
            if product.status != "active" or product.stock_quantity == 0:
                alternatives = list(
                    session.scalars(
                        select(Product)
                        .where(
                            Product.category_id == product.category_id,
                            Product.product_id != product.product_id,
                            Product.status == "active",
                            Product.stock_quantity > 0,
                        )
                        .order_by(
                            func.abs(Product.price_brl - product.price_brl), Product.product_id
                        )
                        .limit(3)
                    )
                )
            promotions = active_promotions(
                session, [product.product_id, *[p.product_id for p in alternatives]]
            )
            return {
                "status": "ok",
                "product": {
                    **product_data(product, category, promotions.get(product.product_id)),
                    "description": product.description,
                    "specs": product.specs,
                    "created_at": product.created_at.isoformat(),
                },
                "alternatives": [
                    product_data(p, category, promotions.get(p.product_id)) for p in alternatives
                ],
                "alternatives_basis": (
                    "Mesma categoria e proximidade de preço de tabela; "
                    "não são sucessores confirmados."
                ),
            }

    def get_active_promotions(self, request: PromotionSearch) -> dict:
        """Consulta campanhas sem inferir vigência a partir do nome ou calendário.

        Args:
            request: Filtros e paginação; campanhas inativas são opcionais.

        Returns:
            Estado registrado e preço de cada campanha ativa, junto ao preço
            atual do produto. Campanhas inativas não oferecem preço promocional.
            Os dados não comprovam quando uma campanha começou ou terminou.
        """
        with self.sessions() as session:
            statement = (
                select(Promotion, Product, Category.name)
                .join(Product, Promotion.product_id == Product.product_id)
                .join(Category, Product.category_id == Category.category_id)
            )
            if not request.include_inactive:
                statement = statement.where(Promotion.is_active.is_(True))
            if request.product_id is not None:
                statement = statement.where(Promotion.product_id == request.product_id)
            if request.campaign:
                statement = statement.where(contains_text(Promotion.description, request.campaign))
            rows = session.execute(
                statement.order_by(Promotion.promotion_id)
                .offset(request.offset)
                .limit(request.limit + 1)
            ).all()
            selected = active_promotions(session, [p.product_id for _, p, _ in rows])
            return {
                "status": "ok",
                "promotions": [
                    {
                        "promotion_id": promotion.promotion_id,
                        "campaign": promotion.description,
                        "is_active": promotion.is_active,
                        "discount_percent": money(promotion.discount_percent),
                        "campaign_price_brl": money(current_price(product, promotion))
                        if promotion.is_active
                        else None,
                        "product": product_data(
                            product, category, selected.get(product.product_id)
                        ),
                    }
                    for promotion, product, category in rows[: request.limit]
                ],
                "has_more": len(rows) > request.limit,
                "limitations": (
                    "Estado registrado, sem datas de vigência. Promoções não cumulativas, "
                    "sem desconto PIX e sujeitas a estoque; não há reserva de preço."
                ),
            }
