"""Falhas controladas da indexação e recuperação de políticas."""


class PolicyIndexingError(ValueError):
    """A fonte ou os vetores não permitem criar um índice íntegro."""
