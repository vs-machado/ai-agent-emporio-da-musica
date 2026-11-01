"""Adaptação dos serviços de atendimento ao tool calling do LangChain."""

from collections.abc import Callable

from langchain_core.tools import StructuredTool
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.schemas.base import Input
from app.schemas.catalog import ProductDetails, ProductSearch, PromotionSearch
from app.schemas.commerce import PaymentQuote, ShippingQuote
from app.schemas.orders import ComplaintRegistration, OrderLookup
from app.schemas.policies import PolicySearch
from app.services.catalog import CatalogService
from app.services.commerce import CommerceService
from app.services.orders import OrderService
from app.services.policies import PolicyRetriever, PolicyService


def service_tool(
    name: str, description: str, schema: type[Input], handler: Callable
) -> StructuredTool:
    def invoke(**kwargs) -> dict:
        try:
            request = schema.model_validate(kwargs)
        except ValidationError:
            return {
                "status": "invalid_input",
                "message": "Dados inválidos para a consulta; confira os parâmetros.",
            }
        try:
            return handler(request)
        except (SQLAlchemyError, TimeoutError, OSError, ValidationError):
            return {
                "status": "unavailable",
                "message": (
                    "Não foi possível consultar ou registrar os dados agora. Não confirme "
                    "informações sem uma consulta bem-sucedida."
                ),
            }

    return StructuredTool.from_function(
        func=invoke,
        name=name,
        description=description,
        args_schema=schema,
        handle_validation_error=(
            "Parâmetros inválidos. Confira os campos e limites definidos no schema da tool."
        ),
    )


def build_tools(
    sessions: sessionmaker[Session], policy_retriever: PolicyRetriever | None = None
) -> list[StructuredTool]:
    """Cria as tools com dependências explícitas, sem inicializar um agente.

    Args:
        sessions: Factory de sessões do PostgreSQL; cada operação gerencia a sua.
        policy_retriever: Serviço de recuperação do manual. Ausente enquanto o
            RAG não estiver configurado; a tool reporta essa indisponibilidade.

    Returns:
        Oito tools com schemas e descrições em português. As operações comerciais
        só simulam condições, e o registro de reclamações não notifica a equipe.
    """
    catalog = CatalogService(sessions)
    commerce = CommerceService(sessions)
    orders = OrderService(sessions)
    policies = PolicyService(policy_retriever)
    definitions = (
        (
            "search_products",
            "Busca instrumentos por nome, categoria, marca no nome e faixa de preço atual. "
            "Retorna preço, promoção, status e estoque consultados. Para encontrar itens "
            "indisponíveis use available_only=false. A loja não comercializa acessórios.",
            ProductSearch,
            catalog.search_products,
        ),
        (
            "get_product_details",
            "Consulta detalhes, especificações, preço atual e disponibilidade de um "
            "instrumento pelo ID. Para itens indisponíveis, sugere alternativas da mesma "
            "categoria sem garantir equivalência ou sucessão.",
            ProductDetails,
            catalog.get_product_details,
        ),
        (
            "get_active_promotions",
            "Consulta promoções registradas. Para perguntar por campanhas inativas, use "
            "include_inactive=true e filtre campaign. Não há datas de vigência; não afirme "
            "quando expiraram. Descontos não cumulativos, sem PIX adicional e sujeitos a estoque.",
            PromotionSearch,
            catalog.get_active_promotions,
        ),
        (
            "get_order_status",
            "Consulta pedido e rastreamento com número e e-mail cadastrado fornecidos pelo "
            "cliente. Não invente e-mail nem consulte pedidos de terceiros. Previsão não "
            "comprova recebimento nem elegibilidade para troca ou garantia.",
            OrderLookup,
            orders.get_order_status,
        ),
        (
            "calculate_payment",
            "Simula PIX, débito, crédito, boleto ou combinação usando produtos e quantidades "
            "do catálogo. Consulta promoções, impede cumulação de descontos e verifica parcelas. "
            "Não recebe preços, não cobra nem cria pedidos. "
            "Respeite requires_confirmation e not_allowed.",
            PaymentQuote,
            commerce.calculate_payment,
        ),
        (
            "calculate_shipping",
            "Consulta o carrinho para estimar frete e prazos. Região deve ser confirmada pelo "
            "cliente, sem inferir cidades metropolitanas. Fora da região local exige cotação, "
            "sem inventar preços. Não agenda entregas.",
            ShippingQuote,
            commerce.calculate_shipping,
        ),
        (
            "register_complaint",
            "Registra relato e contato com protocolo, sem prometer solução. Use a mesma "
            "request_id em retries. Pedido opcional deve corresponder ao e-mail. O registro "
            "não envia à equipe nem garante retorno em 24 horas úteis; informe essa limitação.",
            ComplaintRegistration,
            orders.register_complaint,
        ),
        (
            "search_policy",
            "Recupera trechos do manual para informações institucionais, horário, pagamento, "
            "entrega, trocas, devoluções, garantia, privacidade e escopo. Não substitui consultas "
            "de preço, estoque ou pedidos. Se indisponível ou sem evidência, não invente "
            "políticas e consulte a equipe.",
            PolicySearch,
            policies.search_policy,
        ),
    )
    return [service_tool(*definition) for definition in definitions]
