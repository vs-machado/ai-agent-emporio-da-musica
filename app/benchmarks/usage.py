"""Consumo informado pelos provedores, sem estimar tokens ou preços ausentes."""

import json
from collections import defaultdict


def valid_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def summarize_usage(calls):
    totals = {}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        values = [(call.get("usage") or {}).get(field) for call in calls]
        known = [v for v in values if valid_count(v)]
        totals[field] = sum(known) if known else (0 if not calls else None)
    reported = sum(
        all(
            valid_count((c.get("usage") or {}).get(f))
            for f in ("input_tokens", "output_tokens", "total_tokens")
        )
        for c in calls
    )
    return {
        "status": "no_calls"
        if not calls
        else ("complete" if reported == len(calls) else "partial"),
        "calls": len(calls),
        "calls_with_usage": reported,
        **totals,
    }


def run_token_usage(results):
    usage = {}
    evaluated = [r for r in results if r.get("status") != "skipped"]
    for role, key in (("agent", "model_calls"), ("judge", "judge_model_calls")):
        calls = [call for r in evaluated for call in (r.get(key) or [])]
        groups = defaultdict(list)
        for call in calls:
            groups[call.get("model") or "unknown"].append(call)
        summary = summarize_usage(calls)
        missing = sum(r.get(key) is None for r in evaluated)
        summary["cases_without_records"] = missing
        if missing:
            summary["status"] = "partial" if calls else "not_recorded"
            if not calls:
                for field in ("input_tokens", "output_tokens", "total_tokens"):
                    summary[field] = None
        summary["by_model"] = {model: summarize_usage(items) for model, items in groups.items()}
        usage[role] = summary
    usage["embeddings"] = {"status": "not_recorded", "total_tokens": None}
    usage["cost"] = {"status": "not_calculated", "reason": "provider_prices_not_configured"}
    return usage


class JudgeUsageRecorder:
    """Hook HTTP do cliente OpenAI: inclui respostas descartadas/retries do Ragas.

    Somente modelo e usage são retidos; prompts, respostas e credenciais não entram.
    Respostas sem usage permanecem desconhecidas, inclusive erros do provedor.
    """

    def __init__(self):
        self.calls = []

    async def record_request(self, request):
        if not request.url.path.endswith("/chat/completions"):
            return
        await request.aread()
        try:
            model = json.loads(request.content).get("model")
        except (ValueError, AttributeError):
            model = None
        request.extensions["benchmark_usage_index"] = len(self.calls)
        self.calls.append({"model": model, "usage": None})

    async def record_response(self, response):
        if not response.request.url.path.endswith("/chat/completions"):
            return
        await response.aread()
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        raw = payload.get("usage") or {}
        usage = (
            {
                "input_tokens": raw.get("prompt_tokens"),
                "output_tokens": raw.get("completion_tokens"),
                "total_tokens": raw.get("total_tokens"),
                "input_token_details": raw.get("prompt_tokens_details"),
                "output_token_details": raw.get("completion_tokens_details"),
            }
            if raw
            else None
        )
        index = response.request.extensions.get("benchmark_usage_index")
        if index is None:
            self.calls.append({"model": payload.get("model"), "usage": usage})
        else:
            self.calls[index].update(
                model=payload.get("model") or self.calls[index]["model"], usage=usage
            )
