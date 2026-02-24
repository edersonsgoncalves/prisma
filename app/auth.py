"""
auth.py — Autenticação por sessão usando itsdangerous + bcrypt.
Fornece: middleware de sessão, login, logout, proteção de rotas.
"""
import os
from pathlib import Path
from functools import wraps
from typing import Optional

from dotenv import load_dotenv
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import bcrypt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.models import Usuario

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

SECRET_KEY   = os.getenv('APP_SECRET_KEY', 'dev-secret-troque-em-producao')
COOKIE_NAME  = os.getenv('SESSION_COOKIE_NAME', 'finorg_session')
MAX_AGE_SEC  = 60 * 60 * 8   # 8 horas

_serializer  = URLSafeTimedSerializer(SECRET_KEY)
pwd_context  = CryptContext(schemes=["bcrypt"], deprecated="auto")


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
        secure=False,   # True se usar HTTPS
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
        # Retorna redirect — FastAPI aceita exceção de Response
        raise HTTPException(
            status_code=307,
            headers={"Location": "/login"},
        )
    return sessao


# ──────────────────────────────────────────────
# Login / Logout
# ──────────────────────────────────────────────

def verificar_credenciais(db: Session, login: str, senha: str) -> Optional[Usuario]:
    """Busca usuário e valida senha. Retorna o objeto Usuario ou None."""
    usuario = db.query(Usuario).filter(
        Usuario.usuario_login == login,
        Usuario.usuario_ativo == 1,
    ).first()
    if not usuario:
        return None
    
    # Workaround para erro de "72 bytes" no passlib + bcrypt 4.0+ no Windows
    try:
        # Se for um hash bcrypt válido, tentamos validar diretamente
        if usuario.usuario_senha.startswith(("$2a$", "$2b$", "$2y$")):
            input_pwd = senha.encode('utf-8')
            stored_hash = usuario.usuario_senha.encode('utf-8')
            if bcrypt.checkpw(input_pwd, stored_hash):
                return usuario
            return None
    except Exception:
        # Se falhar a verificação direta (ex: formato inválido), 
        # tentamos o passlib como fallback (embora ele provavelmente vá falhar com o mesmo ValueError)
        pass

    if not pwd_context.verify(senha, usuario.usuario_senha):
        return None
    return usuario


def hash_senha(senha: str) -> str:
    """Gera hash bcrypt de uma senha em texto plano."""
    return pwd_context.hash(senha)
