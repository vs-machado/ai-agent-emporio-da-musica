"""Casos revisados, com procedência explícita e referências independentes das respostas."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_CASES = Path(__file__).with_name("cases.json")


class ReferenceCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    args: dict = Field(default_factory=dict)


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str


class ToolPlan(BaseModel):
    """Um caminho válido, sem transformar chamadas complementares em erros."""

    model_config = ConfigDict(extra="forbid")
    name: str
    required_tools: list[str]
    optional_tools: list[str] = Field(default_factory=list)
    ordered: bool = False

    @model_validator(mode="after")
    def validate_names(self):
        names = [*self.required_tools, *self.optional_tools]
        if any(not name.strip() for name in names) or len(names) != len(set(names)):
            raise ValueError("Tools obrigatórias e opcionais devem ser únicas e não vazias")
        return self


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
    optional_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    tool_plans: list[ToolPlan] = Field(default_factory=list)
    auth_customer_id: int | None = Field(default=None, gt=0)
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

    @property
    def accepted_tool_plans(self) -> list[ToolPlan]:
        return self.tool_plans or [ToolPlan(name="default", required_tools=self.expected_tools)]

    @model_validator(mode="after")
    def validate_tool_contract(self):
        for names in (self.expected_tools, self.optional_tools, self.forbidden_tools):
            if len(names) != len(set(names)) or any(not name.strip() for name in names):
                raise ValueError("Listas de tools devem conter nomes únicos e não vazios")
        if len({plan.name for plan in self.tool_plans}) != len(self.tool_plans):
            raise ValueError("Planos de tools devem ter nomes únicos")
        for plan in self.accepted_tool_plans:
            allowed = set(plan.required_tools + plan.optional_tools + self.optional_tools)
            if allowed & set(self.forbidden_tools):
                raise ValueError("Uma tool não pode ser permitida e proibida no mesmo caso")
        return self


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    source: str
    cases: list[BenchmarkCase]

    def tool_coverage(self, available_tools: set[str], selected=None) -> dict:
        """Valida nomes contra o registro real e expõe lacunas no gabarito."""
        required, optional = set(), set()
        for case in self.cases if selected is None else selected:
            names = (
                {call.name for call in case.reference_calls}
                | set(case.forbidden_tools)
                | set(case.expected_tools)
            )
            optional.update(case.optional_tools)
            names.update(case.optional_tools)
            for plan in case.accepted_tool_plans:
                required.update(plan.required_tools)
                optional.update(plan.optional_tools)
                names.update(plan.required_tools + plan.optional_tools)
            unknown = names - available_tools
            if unknown:
                raise ValueError(f"Tools desconhecidas no caso {case.id}: {sorted(unknown)}")
        return {
            "available": sorted(available_tools),
            "required_in_some_plan": sorted(required),
            "optional": sorted(optional),
            "not_required_by_any_case": sorted(available_tools - required),
        }


def load_dataset(path: Path = DEFAULT_CASES) -> Dataset:
    dataset = Dataset.model_validate_json(path.read_text(encoding="utf-8"))
    ids = [case.id for case in dataset.cases]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Dataset vazio ou com IDs duplicados")
    return dataset
