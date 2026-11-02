"""Casos revisados, com procedência explícita e referências independentes das respostas."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CASES = Path(__file__).with_name("cases.json")


class ReferenceCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    args: dict = Field(default_factory=dict)


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source_turn_id: int | None = None
    origin: Literal["database", "user_requested"] = "database"
    question: str
    source_question: str | None = None
    history: list[HistoryMessage] = Field(default_factory=list)
    expected_tools: list[str]
    reference_calls: list[ReferenceCall]
    expected_sections: list[str] = Field(default_factory=list)
    reference: str | None = None
    forbidden_patterns: list[str] = Field(default_factory=list)
    required_patterns: list[str] = Field(default_factory=list)
    catalog: (
        Literal[
            "all",
            "bateria",
            "promotions",
            "violao",
            "violao_acustico",
            "violao_sete_cordas",
            "nylon",
            "aco",
            "teclado",
            "teclado_2000",
            "flauta",
        ]
        | None
    ) = None


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    source: str
    cases: list[BenchmarkCase]


def load_dataset(path: Path = DEFAULT_CASES) -> Dataset:
    dataset = Dataset.model_validate_json(path.read_text(encoding="utf-8"))
    ids = [case.id for case in dataset.cases]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Dataset vazio ou com IDs duplicados")
    return dataset
