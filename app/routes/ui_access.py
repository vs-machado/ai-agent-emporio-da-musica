"""Isolamento dos identificadores de estado e da fila do Gradio."""

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from app.services.auth import COOKIE_NAME


class UIAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if not path.startswith("/ui/"):
            return await call_next(request)
        service = request.app.state.auth_service
        user = await run_in_threadpool(service.authenticate, request.cookies.get(COOKIE_NAME))
        if user is None:
            if path == "/ui/" and request.method == "GET":
                return RedirectResponse("/auth")
            return JSONResponse({"detail": "Entre na sua conta."}, status_code=401)

        # A interface usa somente a fila normal; APIs alternativas de execução e
        # streams de mídia não fazem parte deste chat e não devem expor eventos.
        api_path = path.removeprefix("/ui/gradio_api/")
        if api_path.startswith(("call/", "stream/", "run/", "api/", "component_server")):
            return JSONResponse({"detail": "Recurso indisponível."}, status_code=404)

        session_hash = request.query_params.get("session_hash")
        if api_path.startswith("heartbeat/"):
            session_hash = api_path.removeprefix("heartbeat/")
        if request.method == "POST" and "application/json" in request.headers.get(
            "content-type", ""
        ):
            try:
                body = await request.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                session_hash = body.get("session_hash", session_hash)
        if session_hash is not None:
            if not isinstance(session_hash, str) or not 1 <= len(session_hash) <= 128:
                return JSONResponse({"detail": "Sessão inválida."}, status_code=400)
            owned = await run_in_threadpool(service.own_ui_session, user, session_hash)
            if not owned:
                return JSONResponse({"detail": "Sessão não encontrada."}, status_code=404)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
