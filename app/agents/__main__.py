"""Executa e persiste um turno de atendimento pelo terminal."""

import argparse
import sys
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.agents.factory import build_chat_service
from app.config import Settings
from app.database import create_database_engine, create_session_factory
from app.rag.retrieval import LlamaIndexPolicyRetriever
from app.schemas.chat import ChatRequest
from app.tools import build_tools


def main() -> int:
    parser = argparse.ArgumentParser(description="Executa um turno do agente de atendimento.")
    parser.add_argument("message", help="Mensagem do cliente")
    parser.add_argument("--conversation-id", type=UUID, help="UUID da conversa a continuar")
    parser.add_argument("--request-id", type=UUID, help="ID a reutilizar em retries do mesmo turno")
    args = parser.parse_args()
    try:
        settings = Settings()
        payload = {"message": args.message}
        if args.conversation_id:
            payload["conversation_id"] = args.conversation_id
        if args.request_id:
            payload["request_id"] = args.request_id
        request = ChatRequest.model_validate(payload)
        engine = create_database_engine(settings)
        try:
            session_factory = create_session_factory(engine)
            tools = build_tools(session_factory, LlamaIndexPolicyRetriever(engine, settings))
            result = build_chat_service(settings, tools, session_factory=session_factory).chat(
                request
            )
        finally:
            engine.dispose()
        print(result.response.model_dump_json(indent=2))
        return 0 if result.response.status == "ok" else 1
    except (ValueError, SQLAlchemyError):
        print("Dados ou configuração inválidos; confira o ambiente e a mensagem.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
