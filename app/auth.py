"""
auth.py — Autenticação por sessão usando itsdangerous + bcrypt.
Fornece: middleware de sessão, login, logout, proteção de rotas.
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Request, HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import bcrypt
from sqlalchemy.orm import Session

from app.models import Usuario

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_INSECURE_SECRET_DEFAULT = "dev-secret-troque-em-producao"


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _load_secret_key() -> str:
    secret = os.getenv("APP_SECRET_KEY")
    allow_insecure = _env_flag("ALLOW_INSECURE_APP_SECRET", False)
    if not secret or secret == _INSECURE_SECRET_DEFAULT:
        if allow_insecure:
            return _INSECURE_SECRET_DEFAULT
        raise RuntimeError(
            "APP_SECRET_KEY ausente ou inseguro. Defina um segredo forte no ambiente "
            "(ou ALLOW_INSECURE_APP_SECRET=true apenas para desenvolvimento local explícito)."
        )
    return secret


def _cookie_secure_default() -> bool:
    if os.getenv("SESSION_COOKIE_SECURE") is not None:
        return _env_flag("SESSION_COOKIE_SECURE", False)
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    return app_env in {"prod", "production"}


SECRET_KEY = _load_secret_key()
COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "prisma_session")
MAX_AGE_SEC = 60 * 60 * 8  # 8 horas
COOKIE_SECURE = _cookie_secure_default()

_serializer = URLSafeTimedSerializer(SECRET_KEY)


# ──────────────────────────────────────────────
# Helpers de sessão (cookie assinado)
# ──────────────────────────────────────────────


def criar_sessao(response, usuario_id: int, usuario_login: str) -> None:
    """Assina e grava o cookie de sessão na resposta."""
    payload = {"id": usuario_id, "login": usuario_login}
    token = _serializer.dumps(payload)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=MAX_AGE_SEC,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
    )


def ler_sessao(request: Request) -> Optional[dict]:
    """Valida e retorna o payload da sessão, ou None se inválida/expirada."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        return _serializer.loads(token, max_age=MAX_AGE_SEC)
    except (BadSignature, SignatureExpired):
        return None


def encerrar_sessao(response) -> None:
    """Remove o cookie de sessão."""
    response.delete_cookie(COOKIE_NAME)


# ──────────────────────────────────────────────
# Dependência FastAPI — usuário atual
# ──────────────────────────────────────────────


def get_usuario_atual(request: Request) -> dict:
    """
    Retorna dados do usuário logado.
    Lança 401 se não autenticado (use em rotas API/JSON).
    Para rotas HTML, use require_login().
    """
    sessao = ler_sessao(request)
    if not sessao:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return sessao


def get_usuario_opcional(request: Request) -> Optional[dict]:
    """Retorna sessão ou None — sem lançar exceção."""
    return ler_sessao(request)


# ──────────────────────────────────────────────
# Proteção de rotas HTML (redireciona para login)
# ──────────────────────────────────────────────


def require_login(request: Request) -> dict:
    """
    Dependência para rotas HTML protegidas.
    Redireciona para /login se não autenticado.
    """
    sessao = ler_sessao(request)
    if not sessao:
        # 303 evita reenvio do método original (ex.: POST) ao redirecionar
        raise HTTPException(
            status_code=303,
            headers={"Location": "/login"},
        )
    return sessao


# ──────────────────────────────────────────────
# Login / Logout
# ──────────────────────────────────────────────

_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


def _senha_bytes(senha: str) -> bytes:
    # bcrypt considera apenas os primeiros 72 bytes
    return senha.encode("utf-8")[:72]


def verificar_credenciais(db: Session, login: str, senha: str) -> Optional[Usuario]:
    """Busca usuário e valida senha (bcrypt). Retorna o objeto Usuario ou None."""
    usuario = (
        db.query(Usuario)
        .filter(
            Usuario.usuario_login == login,
            Usuario.usuario_ativo == 1,
        )
        .first()
    )
    if not usuario:
        return None

    hash_armazenado = usuario.usuario_senha or ""
    if not hash_armazenado.startswith(_BCRYPT_PREFIXES):
        return None

    try:
        ok = bcrypt.checkpw(_senha_bytes(senha), hash_armazenado.encode("utf-8"))
    except ValueError:
        # hash malformado ou entrada inválida
        return None

    return usuario if ok else None


def hash_senha(senha: str) -> str:
    """Gera hash bcrypt de uma senha em texto plano."""
    return bcrypt.hashpw(_senha_bytes(senha), bcrypt.gensalt()).decode("utf-8")
