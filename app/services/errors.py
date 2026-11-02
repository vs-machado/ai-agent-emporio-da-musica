"""Falhas de domínio compartilhadas com implementações de infraestrutura."""


class PolicyRetrievalError(RuntimeError):
    """Não há uma versão de políticas disponível para consulta."""


class ChatHistoryError(ValueError):
    """O histórico interno não representa turnos completos válidos."""


class ChatHistoryLimitError(ChatHistoryError):
    """A conversa excede o limite de mensagens de entrada."""


class ChatRequestConflictError(ValueError):
    """O ID do turno já foi utilizado com outra mensagem na conversa."""


class ExternalLookupError(RuntimeError):
    """A fonte externa não pôde fornecer uma resposta válida."""
