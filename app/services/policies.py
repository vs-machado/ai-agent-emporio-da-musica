"""Contrato de retrieval das políticas, independente do agente e do LlamaIndex."""

from typing import Protocol

from app.schemas.policies import PolicyPassage, PolicySearch


class PolicyRetriever(Protocol):
    def search(self, query: str, limit: int) -> list[PolicyPassage]:
        """Recupera trechos do manual com página e seção de origem."""
        ...


class PolicyService:
    def __init__(self, retriever: PolicyRetriever | None = None):
        self.retriever = retriever

    def search_policy(self, request: PolicySearch) -> dict:
        """Consulta exclusivamente o serviço de recuperação do manual.

        Args:
            request: Dúvida e limite de trechos, cada um com até 2.000 caracteres.

        Returns:
            Trechos com origem, página e seção, sem gerar respostas. A ausência
            de retrieval informa indisponibilidade; uma busca vazia não confirma
            uma política. O RAG e seu fallback serão implementados separadamente.
        """
        if self.retriever is None:
            return {
                "status": "unavailable",
                "passages": [],
                "message": (
                    "Consulta às políticas ainda não configurada. Não confirme regras sem "
                    "consultar o manual; solicite orientação da equipe."
                ),
            }
        passages = [
            PolicyPassage.model_validate(p)
            for p in self.retriever.search(request.query, request.limit)[: request.limit]
        ]
        return {
            "status": "ok" if passages else "not_found",
            "passages": [passage.model_dump() for passage in passages],
            "message": (
                "Use apenas os trechos recuperados; se não esclarecerem o caso, consulte a equipe."
            ),
        }
