"""Execução histórica ou nova com o mesmo registro de tools do atendimento."""

import hashlib
import json
import re
from time import perf_counter

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_from_dict
from langchain_core.utils.function_calling import convert_to_openai_tool
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.factory import build_chat_service
from app.benchmarks.reporting import RunLog, redact, snapshot
from app.models import ConversationTurn
from app.rag.retrieval import LlamaIndexPolicyRetriever
from app.schemas.catalog import ProductSearch, PromotionSearch
from app.schemas.chat import ChatRequest
from app.services.catalog import CatalogService
from app.tools import build_tools


class RecordingRetriever:
    def __init__(self, retriever):
        self.retriever = retriever
        self.calls = []

    def search(self, query, limit=5):
        started = perf_counter()
        try:
            result = self.retriever.retrieve(query, limit)
        except Exception as exc:
            self.calls.append(
                {
                    "query": query,
                    "limit": limit,
                    "mode": "unavailable",
                    "error_type": type(exc).__name__,
                    "latency_seconds": perf_counter() - started,
                }
            )
            raise
        self.calls.append(
            {
                "query": query,
                "limit": limit,
                "mode": result.mode,
                "candidate_count": result.candidate_count,
                "latency_seconds": perf_counter() - started,
                "passages": [p.model_dump() for p in result.passages],
            }
        )
        return result.passages


def evidence(messages):
    """Somente evidências realmente expostas ao agente, nunca busca feita pelo avaliador."""
    contexts, passages, calls, outputs = [], [], [], []
    for message in messages:
        if isinstance(message, AIMessage):
            calls.extend(
                {"id": c["id"], "name": c["name"], "args": c["args"]} for c in message.tool_calls
            )
        elif isinstance(message, ToolMessage):
            try:
                payload = json.loads(message.content)
            except (TypeError, ValueError):
                payload = {"unparsed": message.text}
            outputs.append(
                {"name": message.name, "call_id": message.tool_call_id, "result": payload}
            )
            if message.name == "search_policy" and isinstance(payload, dict):
                for passage in payload.get("passages", []):
                    passages.append(passage)
                    contexts.append(f"{passage['title']}\n{passage['text']}")
            else:
                contexts.append(json.dumps(payload, ensure_ascii=False))
    return {
        "contexts": list(dict.fromkeys(contexts)),
        "passages": passages,
        "calls": calls,
        "outputs": outputs,
    }


def historical_turn(engine, case):
    with Session(engine) as session:
        turn = session.get(ConversationTurn, case.source_turn_id)
        source_question = case.source_question or case.question
        if turn is None or turn.message.strip() != source_question.strip():
            raise ValueError("Turno de origem ausente ou diferente da pergunta revisada")
        previous = session.scalars(
            select(ConversationTurn)
            .where(
                ConversationTurn.conversation_id == turn.conversation_id,
                ConversationTurn.position < turn.position,
            )
            .order_by(ConversationTurn.position)
        ).all()
        history = messages_from_dict([m for t in previous for m in t.messages])
        return messages_from_dict(turn.messages), history, dict(turn.response)


def catalog_oracle(catalog, case):
    """Snapshot independente para avaliar fatos atuais; nunca entra na faithfulness."""
    if case.catalog == "promotions":
        result = catalog.get_active_promotions(PromotionSearch(limit=20))
        products = [
            p["product"]
            for p in result["promotions"]
            if p["is_active"] and p["product"]["available"]
        ]
    else:
        queries = {
            "bateria": "bateria",
            "violao": "violão",
            "violao_acustico": "violão acústico",
            "violao_sete_cordas": "violão 7 cordas",
            "nylon": "violão nylon",
            "aco": "violão aço",
            "teclado": "teclado",
            "teclado_2000": "teclado",
            "flauta": "flauta",
        }
        result = catalog.search_products(
            ProductSearch(
                query=queries.get(case.catalog),
                limit=20,
                max_price=2000 if case.catalog == "teclado_2000" else None,
            )
        )
        products = result["products"]
    return {"products": products, "has_more": result["has_more"]}


def checks(case, response, actual, oracle=None):
    found = {p["section"] for p in actual["passages"]}
    expected = set(case.expected_sections)
    result = {
        "required_patterns": {
            p: bool(re.search(p, response, re.I | re.S)) for p in case.required_patterns
        },
        "forbidden_patterns_absent": {
            p: not bool(re.search(p, response, re.I | re.S)) for p in case.forbidden_patterns
        },
        "expected_sections": sorted(expected),
        "retrieved_sections": sorted(found),
        "section_recall": len(found & expected) / len(expected) if expected else None,
        "has_evidence": bool(actual["contexts"]),
    }
    if oracle is not None:
        matching = [p for p in oracle["products"] if p["name"].casefold() in response.casefold()]
        result["presents_available_model"] = bool(matching) if oracle["products"] else None
        result["current_oracle_product_count"] = len(oracle["products"])
        if case.catalog == "all" and oracle["products"]:
            result["presents_cheapest_available_model"] = (
                oracle["products"][0]["name"].casefold() in response.casefold()
            )
        if case.catalog == "promotions" and matching:
            result["promotion_has_price_and_discount"] = bool(
                re.search(r"R\$", response) and re.search(r"\d+(?:[.,]\d+)?\s*%", response)
            )
    return result


async def run_benchmark(
    engine, settings, dataset, scorer, *, mode, output, judge_config, cases=None
):
    config = snapshot(engine, settings)
    sessions = sessionmaker(engine)
    recorder = RecordingRetriever(LlamaIndexPolicyRetriever(engine, settings))
    tools = build_tools(sessions, recorder)
    selected = [c for c in dataset.cases if cases is None or c.id in cases]
    if not selected:
        raise ValueError("Nenhum caso selecionado")
    manifest = {
        "mode": mode,
        "configuration": config,
        "judge": judge_config,
        "configuration_scope": "current_run_not_historical_configuration",
        "dataset": dataset.model_dump(),
        "dataset_sha256": hashlib.sha256(dataset.model_dump_json().encode()).hexdigest(),
        "selected_cases": [c.id for c in selected],
        "tools": [convert_to_openai_tool(t) for t in tools],
        "historical_tools": "Todas as chamadas e resultados registrados no histórico",
        "notes": [
            "ToolCallF1 exige igualdade exata de nomes/argumentos; recall compara nomes.",
            "ToolCallAccuracy 0.4.3 compara argumentos de referência mas ignora chaves extras.",
            "Faithfulness usa apenas evidências expostas ao agente, inclusive histórico.",
            "Ausência de contexto é registrada como skipped, não como sucesso.",
            "Histórico não registra modo de retrieval nem configuração antiga completa.",
            "Oracle atual de catálogo não comprova estoque no instante histórico.",
        ],
    }
    log = RunLog(output, manifest)
    results = []
    status = "completed"
    service = build_chat_service(settings, tools) if mode == "live" else None
    catalog = CatalogService(sessions)
    log.event("run_started", selected_cases=manifest["selected_cases"])
    try:
        for case in selected:
            started = perf_counter()
            recorder.calls.clear()
            log.event("case_started", case_id=case.id)
            if mode == "historical" and case.source_turn_id is None:
                result = {
                    "case_id": case.id,
                    "status": "skipped",
                    "reason": "no_historical_source",
                    "question": case.question,
                }
                results.append(result)
                log.event("case_finished", **result)
                log.write("partial.json", {"results": results})
                continue
            try:
                if mode == "historical":
                    messages, history, response = historical_turn(engine, case)
                else:
                    history = [
                        HumanMessage(content=m.content)
                        if m.role == "user"
                        else AIMessage(content=m.content)
                        for m in case.history
                    ]
                    turn = service.chat(ChatRequest(message=case.question), history)
                    messages, response = turn.messages, turn.response.model_dump(mode="json")
                actual = evidence([*history, *messages])
                current = evidence(messages)
                question = "\n".join(
                    [
                        *(m.content for m in case.history if m.role == "user"),
                        (case.source_question or case.question)
                        if mode == "historical"
                        else case.question,
                    ]
                )
                # Logs e judge recebem somente a versão minimizada.
                question, text = redact(question), redact(response["message"])
                contexts = redact(actual["contexts"])
                log.event(
                    "case_captured",
                    case_id=case.id,
                    response=text,
                    tool_calls=current["calls"],
                    tool_outputs=current["outputs"],
                    contexts=contexts,
                    retrieval=recorder.calls,
                )
                metrics = await scorer.score(case, messages, text, contexts, question)
                oracle = catalog_oracle(catalog, case) if case.catalog else None
                result = {
                    "case_id": case.id,
                    "source_turn_id": case.source_turn_id,
                    "status": response.get("status"),
                    "question": question,
                    "benchmark_question": case.question,
                    "response": text,
                    "model": response.get("model"),
                    "used_fallback": response.get("used_fallback"),
                    "model_calls": [
                        {
                            "model": m.response_metadata.get("model_name"),
                            "usage": m.usage_metadata,
                            "finish_reason": m.response_metadata.get("finish_reason"),
                        }
                        for m in messages
                        if isinstance(m, AIMessage)
                    ],
                    "tool_calls": current["calls"],
                    "tool_outputs": current["outputs"],
                    "history_message_count": len(history),
                    "public_history": [
                        {"role": m.type, "content": redact(m.text)}
                        for m in history
                        if isinstance(m, (HumanMessage, AIMessage))
                    ],
                    "contexts": contexts,
                    "retrieval": recorder.calls if mode == "live" else None,
                    "metrics": metrics,
                    "checks": checks(case, text, actual, oracle if mode == "live" else None),
                    "current_catalog_oracle": oracle,
                    "elapsed_seconds": round(perf_counter() - started, 3),
                }
                if response.get("status") != "ok" or any(
                    m["status"] == "error" for m in metrics.values()
                ):
                    status = "partial"
            except Exception as exc:
                status = "partial"
                result = {"case_id": case.id, "status": "error", "error_type": type(exc).__name__}
            results.append(result)
            log.event("case_finished", **result)
            log.write("partial.json", {"results": results})
        # Detectar mudanças de índice/configuração durante a execução.
        final_config = snapshot(engine, settings)
        if config["rag"]["active_index"] != final_config["rag"]["active_index"]:
            status = "partial"
            log.event("active_index_changed", final_index=final_config["rag"]["active_index"])
    except BaseException:
        log.finish(results, "interrupted")
        raise
    return log.directory, log.finish(results, status)
