"""Provisiona contas de demonstração para todos os clientes ingeridos."""

import os

from sqlalchemy import select

from app.config import Settings
from app.database import create_database_engine, create_session_factory
from app.models import Customer, Order, User
from app.services.auth import passwords


def seed_users(sessions, password: str) -> list[dict]:
    if not 10 <= len(password) <= 128:
        raise ValueError("DEMO_PASSWORD deve ter de 10 a 128 caracteres.")
    result = []
    with sessions.begin() as session:
        customers = session.scalars(select(Customer).order_by(Customer.customer_id)).all()
        if not customers:
            raise ValueError("Importe os clientes antes de executar o seed de contas.")
        customers_by_email: dict[str, list[Customer]] = {}
        for customer in customers:
            customers_by_email.setdefault(customer.email.strip().casefold(), []).append(customer)
        for email, matching_customers in customers_by_email.items():
            # Uma conta por e-mail, sem combinar pedidos de cadastros duplicados.
            customer = matching_customers[0]
            customer_id = customer.customer_id
            user = session.scalar(select(User).where(User.email == email))
            linked = session.scalar(select(User).where(User.customer_id == customer_id))
            if linked is not None and linked.email != email:
                # Atualiza as contas do seed antigo sem apagar histórico ou redefinir senha.
                if user is None and linked.email in {"demo.a@example.test", "demo.b@example.test"}:
                    linked.email = email
                    user = linked
                else:
                    raise ValueError(
                        f"O cliente {customer_id} já possui uma conta com outro e-mail."
                    )
            if user is None:
                user = User(
                    email=email,
                    name=customer.name.strip()[:200],
                    customer_id=customer_id,
                    password_hash=passwords.hash(password),
                )
                session.add(user)
                session.flush()
            elif user.customer_id != customer_id:
                raise ValueError(f"A conta {email} já existe com outro vínculo.")
            if not user.name:
                user.name = customer.name.strip()[:200]
            orders = list(
                session.scalars(
                    select(Order.order_id)
                    .where(Order.customer_id == customer_id)
                    .order_by(Order.order_id)
                )
            )
            result.append(
                {
                    "email": email,
                    "customer_id": customer_id,
                    "orders": orders,
                    "duplicate_customer_ids": [item.customer_id for item in matching_customers[1:]],
                }
            )
    return result


def main():
    password = os.environ.get("DEMO_PASSWORD", "")
    engine = create_database_engine(Settings())
    try:
        for account in seed_users(create_session_factory(engine), password):
            print(
                f"{account['email']} | cliente={account['customer_id']} "
                f"| pedidos={account['orders']}"
            )
            if account["duplicate_customer_ids"]:
                print(
                    f"E-mail repetido: mantido cliente {account['customer_id']}; "
                    f"sem vínculo adicional com {account['duplicate_customer_ids']}."
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
