"""Senhas Argon2, JWT e identidade confiável para as operações do agente."""

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.models import UISession, User

passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash(token_urlsafe(32))
COOKIE_NAME = "emporio_session"


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    customer_id: int | None
    email: str
    name: str | None = None


current_principal: ContextVar[Principal | None] = ContextVar("current_principal", default=None)


class AuthService:
    def __init__(self, sessions, settings):
        self.sessions = sessions
        self.settings = settings
        self.secret = (
            settings.auth_jwt_secret.get_secret_value()
            if settings.auth_jwt_secret
            else token_urlsafe(48)
        )

    def register(self, email: str, password: str, name: str) -> Principal:
        name = name.strip()
        if not 1 <= len(name) <= 200:
            raise ValueError("O nome deve ter de 1 a 200 caracteres.")
        # Nunca associa cadastro público a clientes importados por e-mail digitado.
        user = User(email=email.casefold(), name=name, password_hash=passwords.hash(password))
        try:
            with self.sessions.begin() as session:
                session.add(user)
                session.flush()
                return Principal(user.user_id, None, user.email, user.name)
        except IntegrityError as exc:
            raise ValueError("Não foi possível criar a conta com esse e-mail.") from exc

    def login(self, email: str, password: str) -> str | None:
        with self.sessions() as session:
            user = session.scalar(select(User).where(User.email == email.casefold()))
            valid = passwords.verify(password, user.password_hash if user else DUMMY_HASH)
            if not user or not valid:
                return None
            now = datetime.now(UTC)
            return jwt.encode(
                {
                    "sub": str(user.user_id),
                    "ver": user.token_version,
                    "iat": now,
                    "exp": now + timedelta(minutes=self.settings.auth_token_minutes),
                    "iss": "emporio-da-musica",
                    "aud": "emporio-atendimento",
                },
                self.secret,
                algorithm="HS256",
            )

    def authenticate(self, token: str | None) -> Principal | None:
        if not token:
            return None
        try:
            claims = jwt.decode(
                token,
                self.secret,
                algorithms=["HS256"],
                issuer="emporio-da-musica",
                audience="emporio-atendimento",
                options={"require": ["sub", "ver", "iat", "exp"]},
            )
            user_id = UUID(claims["sub"])
        except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
            return None
        with self.sessions() as session:
            user = session.get(User, user_id)
            if user is None or user.token_version != claims["ver"]:
                return None
            return Principal(user.user_id, user.customer_id, user.email, user.name)

    def logout(self, principal: Principal) -> None:
        with self.sessions.begin() as session:
            session.execute(
                update(User)
                .where(User.user_id == principal.user_id)
                .values(token_version=User.token_version + 1)
            )

    def own_ui_session(self, principal: Principal, session_hash: str) -> bool:
        with self.sessions.begin() as session:
            session.execute(
                insert(UISession)
                .values(session_hash=session_hash, user_id=principal.user_id)
                .on_conflict_do_nothing(index_elements=["session_hash"])
            )
            return session.get(UISession, session_hash).user_id == principal.user_id
