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

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

SECRET_KEY   = os.getenv('APP_SECRET_KEY', 'dev-secret-troque-em-producao')
COOKIE_NAME  = os.getenv('SESSION_COOKIE_NAME', 'finorg_session')
MAX_AGE_SEC  = 60 * 60 * 8   # 8 horas
COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}

_serializer  = URLSafeTimedSerializer(SECRET_KEY)


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
    usuario = db.query(Usuario).filter(
        Usuario.usuario_login == login,
        Usuario.usuario_ativo == 1,
    ).first()
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
