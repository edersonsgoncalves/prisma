"""
routers/auth.py — Rotas de login e logout.
"""
from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import verificar_credenciais, criar_sessao, encerrar_sessao, ler_sessao

router = APIRouter(tags=["auth"])

BASE_DIR = Path(__file__).resolve().parent.parent
from app.templates import templates


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # Se já logado, redireciona
    if ler_sessao(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("auth/login.html", {"request": request, "erro": None})


@router.post("/login")
async def login_post(
    request: Request,
    login: str = Form(...),
    senha: str = Form(...),
    db: Session = Depends(get_db),
):
    usuario = verificar_credenciais(db, login, senha)
    if not usuario:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "erro": "Login ou senha inválidos."},
            status_code=401,
        )
    response = RedirectResponse(url="/dashboard", status_code=302)
    criar_sessao(response, usuario.usuario_id, usuario.usuario_login)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    encerrar_sessao(response)
    return response
