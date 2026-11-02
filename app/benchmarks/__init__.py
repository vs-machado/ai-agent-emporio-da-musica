"""Avaliações explícitas do atendimento; nunca executadas no startup da API."""

import os

os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
