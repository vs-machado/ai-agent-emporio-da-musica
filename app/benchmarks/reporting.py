"""Relatórios locais duráveis; snapshot por execução e eventos incrementais."""

import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.prompt import SYSTEM_PROMPT
from app.models import PolicyIndex
from app.rag.indexing import processing_config


def redact(value):
    """Minimização básica; o dataset revisado não contém identidades de clientes."""
    if isinstance(value, str):
        value = re.sub(r"[\w.+-]+@[\w.-]+", "[EMAIL]", value)
        return re.sub(
            r"(?<!\w)(?:\+?55\s*)?\(?\d{2}\)?[ .-]*\d{4,5}[ .-]*\d{4}(?!\w)", "[TELEFONE]", value
        )
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REMOVIDO]"
            if any(x in key.lower() for x in ("password", "api_key", "authorization", "secret"))
            else redact(item)
            for key, item in value.items()
        }
    return value


def snapshot(engine, settings):
    with Session(engine) as session:
        index = session.scalar(select(PolicyIndex).where(PolicyIndex.is_active))
        active = (
            None
            if index is None
            else {
                "index_id": str(index.index_id),
                "source": index.source,
                "source_hash": index.source_hash,
                "fingerprint": index.fingerprint,
                "document_version": index.document_version,
                "processing_config": index.processing_config,
            }
        )
    git = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    return {
        "git_commit": git.stdout.strip() if git.returncode == 0 else None,
        "working_tree_dirty": bool(dirty.stdout.strip()),
        "packages": {p: version(p) for p in ("ragas", "langchain", "litellm")},
        "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "source_hashes": {
            name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
            for name in (
                "app/agents/prompt.py",
                "app/agents/runner.py",
                "app/rag/parser.py",
                "app/rag/retrieval.py",
                "app/services/catalog.py",
                "app/benchmarks/metrics.py",
                "app/benchmarks/runner.py",
            )
        },
        "rag": {
            "chunking": "subseção estrutural; divisão entre blocos completos quando excede limite",
            "chunk_overlap_tokens": 0,
            "configured_indexing": processing_config(settings),
            "active_index": active,
            "top_k": settings.rag_top_k,
            "context_tokens": settings.rag_context_tokens,
            "policy_tool_default_limit": 5,
            "policy_tool_max_limit": 5,
            "retrieval": "cosine vector; PostgreSQL portuguese FTS fallback on vector error",
            "expansion": "subseção completa se couber no orçamento; deduplicação estrutural",
        },
        "agent": {
            name: getattr(settings, name)
            for name in (
                "chat_primary_model",
                "chat_fallback_model",
                "chat_model_timeout",
                "chat_max_output_tokens",
                "chat_max_model_calls",
                "chat_max_tool_calls",
                "chat_history_max_messages",
            )
        },
        "temperature": 0,
        "model_retries": 0,
        "primary_enabled": settings.google_api_key is not None,
        "fallback_enabled": settings.openrouter_api_key is not None,
    }


class RunLog:
    def __init__(self, root: Path, manifest: dict):
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        self.directory = root / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.manifest = {
            "run_id": self.run_id,
            "started_at": datetime.now(UTC).isoformat(),
            **manifest,
        }
        self.write("manifest.json", self.manifest)

    def write(self, name, data):
        path = self.directory / name
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(redact(data), stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def event(self, kind, **data):
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    redact({"at": datetime.now(UTC).isoformat(), "event": kind, **data}),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())

    def finish(self, results, status):
        names = {name for r in results for name in r.get("metrics", {})}
        summary = {}
        for name in sorted(names):
            entries = [r["metrics"][name] for r in results if name in r.get("metrics", {})]
            values = [e["value"] for e in entries if e["status"] == "ok"]
            summary[name] = {
                "mean": sum(values) / len(values) if values else None,
                "scored": len(values),
                "errors": sum(e["status"] == "error" for e in entries),
                "skipped": sum(e["status"] == "skipped" for e in entries),
            }
        report = {
            "run_id": self.run_id,
            "status": status,
            "finished_at": datetime.now(UTC).isoformat(),
            "case_count": len(results),
            "summary": summary,
            "results": results,
        }
        self.write("report.json", report)
        self.event("run_finished", status=status, summary=summary)
        return report
