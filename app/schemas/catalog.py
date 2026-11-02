"""Contratos de consulta ao catálogo e às promoções."""

from typing import Annotated

from pydantic import Field, model_validator

from app.schemas.base import Identifier, Input, Money, Text


class ProductSearch(Input):
    query: Text | None = Field(
        default=None, description="Termos do instrumento ou especificações, como violão de nylon"
    )
    category: Text | None = Field(
        default=None, description="Nome ou parte da categoria, no singular ou plural"
    )
    brand: Text | None = Field(default=None, description="Marca a buscar no nome do produto")
    min_price: Money | None = Field(default=None, description="Preço atual mínimo em reais")
    max_price: Money | None = Field(default=None, description="Preço atual máximo em reais")
    available_only: bool = Field(default=True, description="Somente produtos ativos com estoque")
    limit: Annotated[int, Field(strict=True, ge=1, le=20)] = Field(
        default=5, description="Máximo de resultados"
    )
    offset: Annotated[int, Field(strict=True, ge=0, le=10000)] = Field(
        default=0, description="Quantidade de resultados a pular"
    )

    @model_validator(mode="after")
    def validate_price_range(self):
        if self.min_price is not None and self.max_price is not None:
            if self.min_price > self.max_price:
                raise ValueError("Preço mínimo maior que o máximo")
        return self


class ProductDetails(Input):
    product_id: Identifier = Field(description="ID do produto consultado no catálogo")


class PromotionSearch(Input):
    product_id: Identifier | None = Field(default=None, description="Filtrar por produto")
    campaign: Text | None = Field(default=None, description="Nome ou parte da campanha")
    include_inactive: bool = Field(
        default=False, description="Incluir campanhas registradas inativas"
    )
    limit: Annotated[int, Field(strict=True, ge=1, le=20)] = Field(
        default=10, description="Máximo de resultados"
    )
    offset: Annotated[int, Field(strict=True, ge=0, le=10000)] = Field(
        default=0, description="Resultados a pular"
    )
