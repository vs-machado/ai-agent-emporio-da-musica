"""Avaliação sem provedor: evidências reais, métricas, persistência e falhas parciais."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict
from sqlalchemy.orm import sessionmaker

from app.benchmarks.dataset import load_dataset
from app.benchmarks.metrics import RagasScorer
from app.benchmarks.reporting import RunLog, redact
from app.benchmarks.runner import (
    RecordingRetriever,
    checks,
    evidence,
    historical_turn,
    run_benchmark,
)
from app.tools import build_tools


def case(name):
    return next(c for c in load_dataset().cases if c.id == name)


def test_dataset_has_reviewed_questions_covering_six_reports():
    dataset = load_dataset()
    assert len(dataset.cases) == 31
    assert {15, 38, 39, 40, 41, 42, 44, 45, 46, 56} <= {c.source_turn_id for c in dataset.cases}
    assert case("bateria_contextual").history[0].content == "tem bateria?"
    assert "7 dias corridos após o recebimento" in case("arrependimento").reference


def test_revised_questions_preserve_source_and_remove_redundant_case():
    assert case("endereco_horario").question == (
        "onde fica a loja? que horas voces abrem? que hora fecha?"
    )
    assert case("endereco_horario").source_question.startswith("Consulte as políticas")
    assert case("catalogo_hoje").question == "quais produtos voce tem hoje?"
    assert case("catalogo_hoje").source_question == "e o que voce tem hoje"
    assert "catalogo_sem_mais_perguntas" not in {c.id for c in load_dataset().cases}


def test_campo_grande_cases_have_explicit_origin_and_postal_lookup_expectation():
    city = case("frete_campo_grande")
    postal = case("frete_cep_campo_grande")
    assert city.origin == postal.origin == "user_requested"
    assert city.source_turn_id is None and postal.source_turn_id is None
    assert city.expected_sections == postal.expected_sections == ["5.1"]
    assert "lookup_postal_code" in postal.expected_tools
    assert "79002-000" in postal.question


def test_acoustic_case_accepts_electroacoustic_model_from_catalog():
    selected = case("violao_acustico")
    assert selected.catalog == "violao_acustico"
    result = checks(
        selected,
        "Temos o Violão Eletroacústico Yamaha disponível.",
        {"passages": [], "contexts": ["catálogo consultado"]},
        {"products": [{"name": "Violão Eletroacústico Yamaha"}]},
    )
    assert result["presents_available_model"] is True


def test_new_scenarios_are_explicitly_skipped_in_historical_mode(tmp_path, monkeypatch):
    import app.benchmarks.runner as runner

    monkeypatch.setattr(runner, "snapshot", lambda *args: {"rag": {"active_index": None}})
    monkeypatch.setattr(runner, "LlamaIndexPolicyRetriever", lambda *args: None)

    def forbidden(*args):
        pytest.fail("Caso sem origem não deve consultar turnos históricos")

    monkeypatch.setattr(runner, "historical_turn", forbidden)
    _, report = asyncio.run(
        run_benchmark(
            None,
            SimpleNamespace(),
            load_dataset(),
            RagasScorer(),
            mode="historical",
            output=tmp_path,
            judge_config={},
            cases={"frete_campo_grande", "frete_cep_campo_grande"},
        )
    )
    assert report["case_count"] == 2
    assert all(
        r["status"] == "skipped" and r["reason"] == "no_historical_source"
        for r in report["results"]
    )


def test_historical_rephrased_case_still_loads_original_turn(monkeypatch):
    import app.benchmarks.runner as runner

    selected = case("endereco_horario")
    turn = SimpleNamespace(
        message=selected.source_question,
        conversation_id="source",
        position=0,
        messages=messages_to_dict(
            [
                HumanMessage(content=selected.source_question),
                AIMessage(content="Rua 14 de Maio, 3200."),
            ]
        ),
        response={"message": "Rua 14 de Maio, 3200.", "status": "ok"},
    )

    class SourceSession:
        def __init__(self, engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args):
            return turn

        def scalars(self, *args):
            return SimpleNamespace(all=lambda: [])

    monkeypatch.setattr(runner, "Session", SourceSession)
    messages, history, response = historical_turn(None, selected)
    assert messages[0].content == selected.source_question
    assert messages[0].content != selected.question
    assert not history
    assert response["status"] == "ok"


def test_missing_tool_is_zero_and_missing_context_is_not_faithfulness_success():
    scorer = RagasScorer()
    # Ativar juiz fake para verificar motivo de skip sem chamar provedor.
    scorer.metrics["faithfulness"] = object()
    result = asyncio.run(
        scorer.score(
            case("garantia"),
            [AIMessage(content="Veja a ajuda")],
            "Veja a ajuda",
            [],
            "Tem garantia?",
        )
    )
    assert result["tool_call_accuracy"]["value"] == 0
    assert result["required_tool_recall"]["value"] == 0
    assert result["faithfulness"] == {
        "status": "skipped",
        "value": None,
        "reason": "no_retrieved_context",
    }


def test_correct_clarification_needs_no_tools_and_f1_is_not_misreported_as_zero():
    selected = case("rastreamento_sem_identificadores")
    messages = [
        HumanMessage(content=selected.question),
        AIMessage(content="Qual o número do pedido e o e-mail cadastrado?"),
    ]
    result = asyncio.run(
        RagasScorer().score(selected, messages, messages[-1].text, [], selected.question)
    )
    assert result["tool_call_accuracy"]["value"] == 1
    assert result["required_tool_recall"]["value"] == 1
    assert result["tool_call_f1"]["reason"] == "no_expected_calls"


@pytest.mark.parametrize("tool_name", [t.name for t in build_tools(sessionmaker())])
def test_all_eleven_tool_results_are_captured(tool_name):
    payload = (
        {"passages": [{"title": "Política", "text": "Regra", "section": "2"}]}
        if tool_name == "search_policy"
        else {"status": "ok", "marker": tool_name}
    )
    result = evidence(
        [ToolMessage(name=tool_name, tool_call_id="one", content=json.dumps(payload))]
    )
    assert len(result["outputs"]) == 1
    assert result["outputs"][0]["name"] == tool_name
    assert result["contexts"]


def test_live_runner_exposes_same_eleven_tools_as_attendance(tmp_path, monkeypatch):
    import app.benchmarks.runner as runner

    received = []

    def service(settings, tools):
        received.extend(t.name for t in tools)
        return SimpleNamespace(
            chat=lambda *args: SimpleNamespace(
                messages=[HumanMessage(content="Tem garantia?"), AIMessage(content="90 dias")],
                response=SimpleNamespace(
                    model_dump=lambda **kwargs: {"message": "90 dias", "status": "ok"}
                ),
            )
        )

    monkeypatch.setattr(runner, "snapshot", lambda *args: {"rag": {"active_index": None}})
    monkeypatch.setattr(runner, "LlamaIndexPolicyRetriever", lambda *args: None)
    monkeypatch.setattr(runner, "build_chat_service", service)
    directory, report = asyncio.run(
        run_benchmark(
            None,
            SimpleNamespace(),
            load_dataset(),
            RagasScorer(),
            mode="live",
            output=tmp_path,
            judge_config={},
            cases={"garantia"},
        )
    )
    expected = {t.name for t in build_tools(sessionmaker())}
    assert len(received) == 11
    assert set(received) == expected
    manifest = json.loads((directory / "manifest.json").read_text())
    assert {t["function"]["name"] for t in manifest["tools"]} == expected
    assert report["status"] == "completed"


def test_argument_error_is_distinct_from_using_correct_tool_name():
    messages = [
        HumanMessage(content="Tem bateria?"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_products",
                    "args": {"query": "bateria", "category": "instrumentos musicais"},
                    "id": "one",
                }
            ],
        ),
    ]
    result = asyncio.run(
        RagasScorer().score(case("bateria"), messages, "Não temos", [], "Tem bateria?")
    )
    assert result["required_tool_recall"]["value"] == 1
    assert result["tool_call_f1"]["value"] == 0
    # Ragas 0.4.3 compara as chaves de referência e ignora argumentos extras
    # nesta métrica; F1 é necessário para detectar a categoria indevida.
    assert result["tool_call_accuracy"]["value"] == 1


def test_evidence_comes_only_from_observed_results_and_keeps_empty_search_guidance():
    passage = {"title": "Garantia", "text": "90 dias", "section": "8.1"}
    messages = [
        ToolMessage(
            name="search_policy", tool_call_id="a", content=json.dumps({"passages": [passage]})
        ),
        ToolMessage(
            name="search_products",
            tool_call_id="b",
            content=json.dumps({"products": [], "search_guidance": "Amplie a busca"}),
        ),
        ToolMessage(
            name="get_order_status",
            tool_call_id="c",
            content=json.dumps({"status": "ok", "tracking_code": "BRTESTBR"}),
        ),
    ]
    result = evidence(messages)
    assert result["passages"] == [passage]
    assert result["contexts"][0] == "Garantia\n90 dias"
    assert "Amplie a busca" in result["contexts"][1]
    assert "BRTESTBR" in result["contexts"][2]
    assert result["outputs"][2]["name"] == "get_order_status"


def test_judge_failures_remain_errors_and_other_metrics_survive():
    class Metric:
        async def ascore(self, **kwargs):
            raise RuntimeError("secret-api-key")

    scorer = RagasScorer()
    scorer.metrics["faithfulness"] = Metric()
    result = asyncio.run(
        scorer.score(case("garantia"), [], "90 dias", ["90 dias"], "Tem garantia?")
    )
    assert result["faithfulness"]["status"] == "error"
    assert result["tool_call_accuracy"]["status"] == "ok"
    assert "secret-api-key" not in json.dumps(result)


def test_report_retains_events_and_excludes_skips_and_errors_from_mean(tmp_path):
    log = RunLog(tmp_path, {"password": "secret", "question": "email a@b.com"})
    log.event("case_started", phone="(67) 99999-8888")
    report = log.finish(
        [
            {"metrics": {"faithfulness": {"status": "ok", "value": 0.5}}},
            {"metrics": {"faithfulness": {"status": "skipped", "value": None}}},
            {"metrics": {"faithfulness": {"status": "error", "value": None}}},
        ],
        "partial",
    )
    assert report["summary"]["faithfulness"] == {
        "mean": 0.5,
        "scored": 1,
        "errors": 1,
        "skipped": 1,
    }
    assert json.loads((log.directory / "report.json").read_text())["status"] == "partial"
    assert len((log.directory / "events.jsonl").read_text().splitlines()) == 2
    manifest = (log.directory / "manifest.json").read_text()
    assert "secret" not in manifest and "a@b.com" not in manifest
    assert "99999" not in (log.directory / "events.jsonl").read_text()


def test_arrependimento_checks_reject_wrong_deadline_even_with_correct_section():
    actual = {"passages": [{"section": "4.1"}], "contexts": ["7 dias corridos"]}
    result = checks(
        case("arrependimento"), "O prazo é 30 dias corridos após o recebimento.", actual
    )
    assert result["section_recall"] == 1
    assert not all(result["required_patterns"].values())
    assert not all(result["forbidden_patterns_absent"].values())


def test_historical_runner_captures_partial_results_without_mutating_chats(tmp_path, monkeypatch):
    import app.benchmarks.runner as runner

    monkeypatch.setattr(runner, "snapshot", lambda *args: {"rag": {"active_index": None}})
    monkeypatch.setattr(runner, "LlamaIndexPolicyRetriever", lambda *args: None)
    monkeypatch.setattr(runner, "build_tools", lambda *args: [])
    monkeypatch.setattr(
        runner,
        "historical_turn",
        lambda *args: (
            [HumanMessage(content="Tem garantia?"), AIMessage(content="90 dias")],
            [],
            {"message": "90 dias", "status": "ok", "model": "scripted"},
        ),
    )
    directory, report = asyncio.run(
        run_benchmark(
            None,
            SimpleNamespace(),
            load_dataset(),
            RagasScorer(),
            mode="historical",
            output=tmp_path,
            judge_config={"enabled": False},
            cases={"garantia"},
        )
    )
    assert report["case_count"] == 1
    assert report["results"][0]["tool_calls"] == []
    assert (directory / "partial.json").exists()


def test_live_oracle_is_not_injected_into_grounding_context(tmp_path, monkeypatch):
    import app.benchmarks.runner as runner

    observed = []

    class Scorer:
        async def score(self, case, messages, text, contexts, question):
            observed.extend(contexts)
            return {}

    monkeypatch.setattr(runner, "snapshot", lambda *args: {"rag": {"active_index": None}})
    monkeypatch.setattr(runner, "LlamaIndexPolicyRetriever", lambda *args: None)
    monkeypatch.setattr(runner, "build_tools", lambda *args: [])
    service = SimpleNamespace(
        chat=lambda *args: SimpleNamespace(
            messages=[HumanMessage(content="Tem bateria?"), AIMessage(content="Não temos")],
            response=SimpleNamespace(
                model_dump=lambda **kwargs: {"message": "Não temos", "status": "ok"}
            ),
        )
    )
    monkeypatch.setattr(runner, "build_chat_service", lambda *args: service)
    monkeypatch.setattr(
        runner,
        "catalog_oracle",
        lambda *args: {"products": [{"name": "Bateria Yamaha"}], "has_more": False},
    )
    _, report = asyncio.run(
        run_benchmark(
            None,
            SimpleNamespace(),
            load_dataset(),
            Scorer(),
            mode="live",
            output=tmp_path,
            judge_config={},
            cases={"bateria"},
        )
    )
    assert not observed
    assert report["results"][0]["contexts"] == []
    assert report["results"][0]["checks"]["presents_available_model"] is False


def test_interrupted_run_preserves_finished_cases_and_final_report(tmp_path, monkeypatch):
    import app.benchmarks.runner as runner

    monkeypatch.setattr(runner, "snapshot", lambda *args: {"rag": {"active_index": None}})
    monkeypatch.setattr(runner, "LlamaIndexPolicyRetriever", lambda *args: None)
    monkeypatch.setattr(runner, "build_tools", lambda *args: [])

    def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "historical_turn", interrupted)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(
            run_benchmark(
                None,
                SimpleNamespace(),
                load_dataset(),
                RagasScorer(),
                mode="historical",
                output=tmp_path,
                judge_config={},
                cases={"garantia"},
            )
        )
    report_path = next(tmp_path.glob("*/report.json"))
    assert json.loads(report_path.read_text())["status"] == "interrupted"


def test_redaction_never_exports_secret_configuration_values():
    assert redact({"nested": {"google_api_key": "key", "postgres_password": "password"}}) == {
        "nested": {"google_api_key": "[REMOVIDO]", "postgres_password": "[REMOVIDO]"}
    }


def test_retrieval_failure_is_logged_without_sdk_error_details():
    class FailingRetriever:
        def retrieve(self, query, limit):
            raise RuntimeError("secret-provider-url")

    recorder = RecordingRetriever(FailingRetriever())
    with pytest.raises(RuntimeError):
        recorder.search("horário", 3)
    assert recorder.calls[0]["mode"] == "unavailable"
    assert recorder.calls[0]["limit"] == 3
    assert "secret-provider-url" not in json.dumps(recorder.calls)


def test_historical_source_mismatch_is_rejected(engine):
    with pytest.raises(ValueError, match="Turno de origem"):
        historical_turn(engine, case("garantia"))
