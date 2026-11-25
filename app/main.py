"""
main.py — Aplicação FastAPI principal do Pri$ma.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from app.templates import templates

from app.database import engine, Base
from app.models import Usuario
from app.auth import (
    require_login,
    verificar_credenciais,
    criar_sessao,
    encerrar_sessao,
    hash_senha,
    SECRET_KEY,
)
from app.helpers import (
    formata_moeda_brl,
    mostra_data,
    cor_valor,
    mes_por_extenso,
    formata_parcela,
)
from app.csrf import CSRFMiddleware

# Importação dos routers
from app.routers import (
    dashboard,
    extrato,
    contas,
    faturas,
    categorias,
    projetos,
    recorrencias,
    relatorios,
    notificacoes,
    lancamentos,
    lancamentos_faturas,
    lancamentos_transfers,
)
from app.routers import auth as auth_router
from app.routers.ofx import router as ofx_router
from app.routers.pdf_fatura import router as pdf_fatura_router
from app.investimentos.router import router as investimentos_router

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BASE_DIR = Path(__file__).resolve().parent

# Cria tabela de usuários se não existir (as demais já existem)
Base.metadata.create_all(bind=engine, tables=[Usuario.__table__])

app = FastAPI(
    title="Prisma",
    description="Sistema de Gerenciamento de Finanças Pessoais",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    debug=False,
)

# CSRF protection
app.add_middleware(CSRFMiddleware, secret=SECRET_KEY)

# ── Arquivos estáticos ─────────────────────────────
app.mount(
    "/static", StaticFiles(directory=str(BASE_DIR.parent / "static")), name="static"
)

# templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ── Routers ───────────────────────────────────────
app.include_router(auth_router.router)
app.include_router(investimentos_router)
app.include_router(dashboard.router)
app.include_router(extrato.router)
app.include_router(contas.router)
app.include_router(faturas.router)
app.include_router(lancamentos.router)
app.include_router(lancamentos_faturas.router)
app.include_router(lancamentos_transfers.router)
app.include_router(categorias.router)
app.include_router(projetos.router)
app.include_router(recorrencias.router)
app.include_router(relatorios.router)
app.include_router(notificacoes.router)
app.include_router(ofx_router)
app.include_router(pdf_fatura_router)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard")


@app.get("/ping")
async def ping():
    return {"status": "ok", "message": "Pong! Prisma is alive."}
