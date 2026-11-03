"""CLI explícita: python -m app.benchmarks --mode historical|live."""

import argparse
import asyncio
import os
from pathlib import Path

from app.benchmarks.dataset import DEFAULT_CASES, load_dataset
from app.benchmarks.metrics import EvaluationEmbeddings, RagasScorer
from app.benchmarks.runner import run_benchmark
from app.benchmarks.usage import JudgeUsageRecorder
from app.config import Settings
from app.database import create_database_engine


def main():
    parser = argparse.ArgumentParser(description="Benchmark Ragas da tríade e uso de tools")
    parser.add_argument("--mode", choices=("historical", "live"), default="historical")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=Path("benchmark-results"))
    parser.add_argument("--case", action="append", dest="cases", help="ID; pode repetir")
    parser.add_argument("--judge-model", default="gemini-2.5-flash")
    parser.add_argument("--judge-max-tokens", type=int, default=16384)
    parser.add_argument("--metric-timeout", type=float, default=90)
    parser.add_argument(
        "--no-judge", action="store_true", help="Somente tools/checks locais; NÃO avalia a tríade"
    )
    args = parser.parse_args()
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
    settings = Settings()
    dataset = load_dataset(args.dataset)
    if args.cases and set(args.cases) - {c.id for c in dataset.cases}:
        parser.error("ID de caso desconhecido")
    if args.metric_timeout <= 0 or args.judge_max_tokens <= 0:
        parser.error("Timeout e limite de tokens devem ser positivos")
    if not args.no_judge and settings.google_api_key is None:
        parser.error("GOOGLE_API_KEY necessária para o avaliador")
    primary_key = (
        settings.openrouter_api_key
        if settings.chat_primary_model.startswith("openrouter/")
        else settings.google_api_key
    )
    if args.mode == "live" and primary_key is None:
        parser.error("Configure a chave do provedor de CHAT_PRIMARY_MODEL para executar o agente")
    client = None
    judge_usage = None
    if args.no_judge:
        scorer = RagasScorer(timeout=args.metric_timeout)
    else:
        import httpx
        from openai import AsyncOpenAI
        from ragas.llms import llm_factory

        from app.rag.embeddings import build_embeddings

        judge_usage = JudgeUsageRecorder()
        client = AsyncOpenAI(
            api_key=settings.google_api_key.get_secret_value(),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            timeout=args.metric_timeout,
            max_retries=0,
            http_client=httpx.AsyncClient(
                event_hooks={
                    "request": [judge_usage.record_request],
                    "response": [judge_usage.record_response],
                }
            ),
        )
        llm = llm_factory(
            args.judge_model, client=client, temperature=0, max_tokens=args.judge_max_tokens
        )
        scorer = RagasScorer(
            llm, EvaluationEmbeddings(build_embeddings(settings)), timeout=args.metric_timeout
        )
    engine = create_database_engine(settings)

    async def execute():
        try:
            return await run_benchmark(
                engine,
                settings,
                dataset,
                scorer,
                mode=args.mode,
                output=args.output,
                cases=set(args.cases) if args.cases else None,
                judge_usage=judge_usage,
                judge_config={
                    "enabled": not args.no_judge,
                    "model": args.judge_model,
                    "provider": "google_openai_compatible",
                    "temperature": 0,
                    "max_tokens": args.judge_max_tokens,
                    "metric_timeout": args.metric_timeout,
                    "embedding_model": settings.rag_embedding_model,
                    "embedding_task": "RETRIEVAL_QUERY",
                    "strict_tool_order": False,
                },
            )
        finally:
            if client is not None:
                await client.close()

    try:
        directory, report = asyncio.run(execute())
        print(f"Benchmark {report['status']}: {directory / 'report.json'}")
        return 0 if report["status"] == "completed" else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
