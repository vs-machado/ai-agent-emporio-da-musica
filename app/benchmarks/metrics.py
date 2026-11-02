"""Ragas collections 0.4: tríade, correção e chamadas de ferramentas."""

import asyncio
import math

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

    async def score(self, case: BenchmarkCase, messages, response, contexts, question):
        results = {}

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
        # Nome apenas: outra métrica, explicitamente distinta de argumentos exatos.
        predicted_names = {
            c["name"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls
        }
        expected_names = set(case.expected_tools)
        results["required_tool_recall"] = {
            "status": "ok",
            "value": len(predicted_names & expected_names) / len(expected_names)
            if expected_names
            else float(not predicted_names),
        }
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
