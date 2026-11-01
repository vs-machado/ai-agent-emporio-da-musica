"""Falhas de domínio compartilhadas com implementações de infraestrutura."""


class PolicyRetrievalError(RuntimeError):
    """Não há uma versão de políticas disponível para consulta."""
