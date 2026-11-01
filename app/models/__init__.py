"""Modelos registrados no metadata utilizado pelo Alembic."""

from app.models.base import Base
from app.models.category import Category
from app.models.complaint import Complaint
from app.models.customer import Customer
from app.models.order import Order
from app.models.order_item import OrderItem
from app.models.policy_index import PolicyIndex
from app.models.policy_section import PolicySection
from app.models.policy_vector import PolicyVector
from app.models.product import Product
from app.models.promotion import Promotion

__all__ = [
    "Base",
    "Category",
    "Complaint",
    "Customer",
    "Order",
    "OrderItem",
    "PolicyIndex",
    "PolicySection",
    "PolicyVector",
    "Product",
    "Promotion",
]
