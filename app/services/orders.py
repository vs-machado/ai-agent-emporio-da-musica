"""Consulta mínima de pedidos e registro persistente de reclamações."""

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.models import Complaint, Customer, Order, OrderItem, Product
from app.schemas.orders import ComplaintRegistration, OrderLookup
from app.services.catalog import money

ORDER_NOT_FOUND = {
    "status": "not_found",
    "message": "Não foi possível localizar o pedido com os dados informados.",
}


class OrderService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def _verified_order(self, session: Session, order_id: int, email: str) -> Order | None:
        """Exige correspondência de ID e e-mail; não comprova controle da conta."""
        return session.scalar(
            select(Order)
            .join(Customer)
            .where(Order.order_id == order_id, func.lower(Customer.email) == email.casefold())
        )

    def get_order_status(self, request: OrderLookup) -> dict:
        """Consulta pedido mediante correspondência de número e e-mail cadastrado.

        Args:
            request: Dados fornecidos pelo cliente, não inferidos pelo agente.

        Returns:
            Dados mínimos do pedido, sem cadastro pessoal ou preço histórico
            inferido. Falhas não distinguem pedido inexistente de e-mail errado.
            Essa verificação do protótipo não substitui autenticação real.
        """
        with self.sessions() as session:
            order = self._verified_order(session, request.order_id, request.email)
            if order is None:
                return dict(ORDER_NOT_FOUND)
            items = session.execute(
                select(OrderItem, Product.name)
                .join(Product)
                .where(OrderItem.order_id == order.order_id)
                .order_by(OrderItem.product_id)
            )
            return {
                "status": "ok",
                "order_id": order.order_id,
                "order_status": order.status,
                "order_date": order.order_date.isoformat(),
                "total_brl": money(order.total_brl),
                "payment_method": order.payment_method,
                "tracking_code": order.tracking_code,
                "estimated_delivery": order.estimated_delivery.isoformat()
                if order.estimated_delivery
                else None,
                "actual_delivery_date": None,
                "items": [
                    {"product_id": item.product_id, "name": name, "quantity": item.quantity}
                    for item, name in items
                ],
                "limitations": (
                    "A previsão não comprova recebimento. Não é possível aprovar troca, "
                    "devolução ou garantia sem confirmar datas e condições necessárias."
                ),
            }

    def register_complaint(self, request: ComplaintRegistration) -> dict:
        """Registra reclamação com idempotência e vínculo opcional ao pedido.

        Args:
            request: Relato, contato e chave estável da solicitação. Se houver
                pedido, o contato deve corresponder ao e-mail cadastrado.

        Returns:
            Protocolo e status persistidos. A mesma chave com os mesmos dados
            reutiliza o protocolo; dados diferentes resultam em conflito.
            O registro não implica notificação ou acompanhamento pela equipe.
        """
        email = request.contact_email.casefold()
        with self.sessions() as session, session.begin():
            if (
                request.order_id is not None
                and self._verified_order(session, request.order_id, email) is None
            ):
                return dict(ORDER_NOT_FOUND)
            statement = (
                insert(Complaint)
                .values(
                    complaint_id=uuid4(),
                    request_id=request.request_id,
                    contact_email=email,
                    message=request.message,
                    order_id=request.order_id,
                    status="open",
                )
                .on_conflict_do_nothing(index_elements=[Complaint.request_id])
            )
            session.execute(statement)
            complaint = session.scalar(
                select(Complaint).where(Complaint.request_id == request.request_id)
            )
            if (complaint.contact_email, complaint.message, complaint.order_id) != (
                email,
                request.message,
                request.order_id,
            ):
                return {
                    "status": "request_conflict",
                    "message": "A chave da solicitação já foi utilizada com outros dados.",
                }
            return {
                "status": "registered",
                "complaint_id": str(complaint.complaint_id),
                "complaint_status": complaint.status,
                "created_at": complaint.created_at.isoformat(),
                "forwarded_to_team": False,
                "message": (
                    "Reclamação registrada no protótipo. O envio à equipe ainda não está "
                    "integrado; não há garantia operacional de retorno em 24 horas úteis. "
                    "Oriente contato pelos canais oficiais."
                ),
            }
