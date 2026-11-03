"""Autenticação por cookie HttpOnly e página de entrada para o Gradio."""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ConfigDict, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from app.schemas.base import Email, Input
from app.services.auth import COOKIE_NAME, Principal

router = APIRouter(prefix="/auth", tags=["Autenticação"])


class Credentials(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    email: Email
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().casefold() if isinstance(value, str) else value


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        return value.strip() if isinstance(value, str) else value


def optional_user(request: Request) -> Principal | None:
    return request.app.state.auth_service.authenticate(request.cookies.get(COOKIE_NAME))


def get_current_user(request: Request) -> Principal:
    user = optional_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Entre na sua conta para continuar.")
    return user


CurrentUser = Annotated[Principal, Depends(get_current_user)]


class SameOriginMiddleware(BaseHTTPMiddleware):
    """Protege mutações (inclusive login e eventos Gradio) contra CSRF."""

    async def dispatch(self, request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin", "")
            expected = urlsplit(str(request.base_url))
            try:
                actual = urlsplit(origin)
            except ValueError:
                return JSONResponse({"detail": "Origem da solicitação inválida."}, status_code=403)
            if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
                return JSONResponse({"detail": "Origem da solicitação inválida."}, status_code=403)
        return await call_next(request)


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def entry(request: Request):
    if optional_user(request):
        return RedirectResponse("/ui/")
    return HTMLResponse(
        Path(__file__).with_name("auth.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
    )


@router.post("/register", status_code=201)
def register(payload: Registration, request: Request, response: Response):
    try:
        request.app.state.auth_service.register(payload.email, payload.password, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    login(Credentials(email=payload.email, password=payload.password), request, response)
    return {"message": "Conta criada. Entrando no atendimento."}


@router.post("/login")
def login(payload: Credentials, request: Request, response: Response):
    service = request.app.state.auth_service
    token = service.login(payload.email, payload.password)
    if token is None:
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=service.settings.auth_cookie_secure,
        samesite="lax",
        max_age=service.settings.auth_token_minutes * 60,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"message": "Login realizado."}


@router.get("/me")
def me(user: CurrentUser, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {
        "user_id": user.user_id,
        "email": user.email,
        "customer_id": user.customer_id,
        "name": user.name,
    }


@router.post("/logout")
def logout(request: Request, user: CurrentUser):
    request.app.state.auth_service.logout(user)
    response = RedirectResponse("/auth", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response
