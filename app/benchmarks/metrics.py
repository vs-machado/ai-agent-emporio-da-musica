"""Ragas collections 0.4: tríade, correção e chamadas de ferramentas."""

import asyncio
import json
import math

from jsonschema import Draft202012Validator
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from ragas import messages as rm
from ragas.embeddings.base import BaseRagasEmbedding
from ragas.metrics.collections import (
    AnswerRelevancy,
    ContextRelevance,
    FactualCorrectness,
    Faithfulness,
    ToolCallAccuracy,
    ToolCallF1,
)

from app.benchmarks.dataset import BenchmarkCase


def tool_contract_metrics(case: BenchmarkCase, messages, tool_schemas=None):
    """Avalia caminhos admissíveis, contratos e resultados, sem LLM avaliadora."""
    calls = [call for m in messages if isinstance(m, AIMessage) for call in m.tool_calls]
    names = [call["name"] for call in calls]
    observed = set(names)
    invalid_count = sum(len(m.invalid_tool_calls) for m in messages if isinstance(m, AIMessage))
    plans = []
    for plan in case.accepted_tool_plans:
        required = set(plan.required_tools)
        allowed = required | set(plan.optional_tools + case.optional_tools)
        missing = required - observed
        unexpected = observed - allowed
        recall = len(required & observed) / len(required) if required else 1.0
        precision = len(observed & allowed) / len(observed) if observed else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        cursor, ordered = -1, True
        if plan.ordered:
            for name in plan.required_tools:
                cursor = next((i for i in range(cursor + 1, len(names)) if names[i] == name), -1)
                if cursor == -1:
                    ordered = False
                    break
        plans.append(
            {
                "plan": plan.name,
                "recall": recall,
                "precision": precision,
                "f1": f1,
                "success": not missing and not unexpected and ordered and not invalid_count,
                "missing": sorted(missing),
                "unexpected": sorted(unexpected),
                "forbidden": sorted(observed & set(case.forbidden_tools)),
                "order_satisfied": ordered,
            }
        )
    best = max(plans, key=lambda p: (p["success"], p["f1"], p["recall"], p["precision"]))
    results = {
        "required_tool_recall": {"status": "ok", "value": best["recall"]},
        "allowed_tool_precision": {"status": "ok", "value": best["precision"]},
        "tool_plan_f1": {"status": "ok", "value": best["f1"]},
        "tool_plan_success": {
            "status": "ok",
            "value": float(best["success"]),
            "details": {
                key: best[key]
                for key in ("plan", "missing", "unexpected", "forbidden", "order_satisfied")
            },
        },
    }
    schema_failures = []
    if tool_schemas is None:
        results["tool_schema_validity"] = {
            "status": "skipped",
            "value": None,
            "reason": "no_tool_registry",
        }
    else:
        for index, call in enumerate(calls):
            schema = tool_schemas.get(call["name"])
            reason = "unknown_tool" if schema is None else None
            if schema is not None and not Draft202012Validator(schema).is_valid(call["args"]):
                reason = "invalid_arguments"
            if reason:
                schema_failures.append({"index": index, "tool": call["name"], "reason": reason})
        count = len(calls) + invalid_count
        results["tool_schema_validity"] = (
            {
                "status": "ok",
                "value": (len(calls) - len(schema_failures)) / count,
                "details": {"failures": schema_failures, "invalid_tool_calls": invalid_count},
            }
            if count
            else {"status": "skipped", "value": None, "reason": "no_tool_calls"}
        )
    outputs, pending, last_result = {}, {}, {}
    position, orphan_results = 0, []
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                pending.setdefault(call["id"], []).append(position)
                position += 1
        elif isinstance(message, ToolMessage):
            waiting = pending.get(message.tool_call_id, [])
            if waiting:
                index = waiting.pop(0)
                last_result[message.tool_call_id] = index
                outputs.setdefault(index, []).append(message)
            elif message.tool_call_id in last_result:
                outputs[last_result[message.tool_call_id]].append(message)
            else:
                orphan_results.append({"tool": message.name, "reason": "unmatched_result"})
    successful = 0
    failed = []
    for index, call in enumerate(calls):
        matching = outputs.get(index, [])
        reason = None
        if len(matching) != 1:
            reason = "missing_or_duplicate_result"
        else:
            output = matching[0]
            if output.status == "error" or (output.name and output.name != call["name"]):
                reason = "tool_error"
            else:
                try:
                    payload = json.loads(output.text)
                except (ValueError, TypeError):
                    payload = None
                if not isinstance(payload, dict):
                    reason = "invalid_result"
                elif payload.get("status") in {"invalid_input", "unavailable", "request_conflict"}:
                    reason = "failed_result"
        if reason:
            failed.append({"index": index, "tool": call["name"], "reason": reason})
        else:
            successful += 1
    count = len(calls) + invalid_count + len(orphan_results)
    results["tool_execution_success"] = (
        {
            "status": "ok",
            "value": successful / count,
            "details": {"failures": failed + orphan_results, "invalid_tool_calls": invalid_count},
        }
        if count
        else {"status": "skipped", "value": None, "reason": "no_tool_calls"}
    )
    return results


class EvaluationEmbeddings(BaseRagasEmbedding):
    """Adapta o embedding já configurado para a relevância da resposta do Ragas."""

    def __init__(self, model):
        super().__init__()
        self.model = model

    def embed_text(self, text, **kwargs):
        return self.model.get_query_embedding(text)

    async def aembed_text(self, text, **kwargs):
        return await asyncio.to_thread(self.embed_text, text)


def ragas_messages(messages):
    converted = []
    for message in messages:
        if isinstance(message, HumanMessage):
            converted.append(rm.HumanMessage(content=message.text))
        elif isinstance(message, AIMessage):
            converted.append(
                rm.AIMessage(
                    content=message.text,
                    tool_calls=[
                        rm.ToolCall(name=c["name"], args=c["args"]) for c in message.tool_calls
                    ]
                    or None,
                )
            )
        elif isinstance(message, ToolMessage):
            converted.append(rm.ToolMessage(content=message.text))
    return converted


class RagasScorer:
    def __init__(self, llm=None, embeddings=None, timeout: float = 90):
        self.timeout = timeout
        self.metrics = {}
        if llm is not None:
            self.metrics = {
                "context_relevance": ContextRelevance(llm=llm),
                "faithfulness": Faithfulness(llm=llm),
                "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
                "factual_correctness": FactualCorrectness(llm=llm),
            }

    async def score(
        self, case: BenchmarkCase, messages, response, contexts, question, *, tool_schemas=None
    ):
        results = tool_contract_metrics(case, messages, tool_schemas)

        async def measure(name, metric, **kwargs):
            try:
                result = await asyncio.wait_for(metric.ascore(**kwargs), self.timeout)
                value = float(result.value)
                if not math.isfinite(value):
                    raise ValueError("Resultado não finito")
                results[name] = {"status": "ok", "value": value}
                if result.reason:
                    results[name]["reason"] = result.reason
            except Exception as exc:
                # Não persistir mensagens de exceção de SDKs: podem conter credenciais.
                results[name] = {"status": "error", "value": None, "error_type": type(exc).__name__}

        transcript = ragas_messages(messages)
        references = [rm.ToolCall(name=c.name, args=c.args) for c in case.reference_calls]
        await measure(
            "tool_call_accuracy",
            ToolCallAccuracy(strict_order=False),
            user_input=transcript,
            reference_tool_calls=references,
        )
        results["tool_call_accuracy"]["diagnostic"] = True
        if references:
            await measure(
                "tool_call_f1", ToolCallF1(), user_input=transcript, reference_tool_calls=references
            )
        else:
            # F1 do Ragas retorna zero até quando ambos os conjuntos estão vazios.
            # Nesses casos, ToolCallAccuracy avalia a ausência correta de chamadas.
            results["tool_call_f1"] = {
                "status": "skipped",
                "value": None,
                "reason": "no_expected_calls",
            }
        results["tool_call_f1"]["diagnostic"] = True
        for name in (
            "context_relevance",
            "faithfulness",
            "answer_relevancy",
            "factual_correctness",
        ):
            if name not in self.metrics:
                reason = "judge_disabled"
            elif name in {"context_relevance", "faithfulness"} and not contexts:
                reason = "no_retrieved_context"
            elif name == "factual_correctness" and not case.reference:
                reason = "no_reference"
            else:
                reason = None
            if reason:
                results[name] = {"status": "skipped", "value": None, "reason": reason}
                continue
            kwargs = {"response": response}
            if name != "factual_correctness":
                kwargs["user_input"] = question
            if name in {"context_relevance", "faithfulness"}:
                kwargs["retrieved_contexts"] = contexts
            if name == "context_relevance":
                kwargs.pop("response")
            if name == "factual_correctness":
                kwargs["reference"] = case.reference
            await measure(name, self.metrics[name], **kwargs)
        return results
