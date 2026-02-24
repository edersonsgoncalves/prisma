"""routers/faturas.py — Faturas de cartão de crédito."""
from pathlib import Path
from datetime import date
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.templates import templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import extract

from app.database import get_db
from app.auth import require_login
from app.models import FaturaCartao, Operacao, ContaBancaria

router = APIRouter(prefix="/faturas", tags=["faturas"])
BASE_DIR = Path(__file__).resolve().parent.parent
# templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@router.get("", response_class=HTMLResponse)
async def listar_faturas(
    request: Request,
    sessao=Depends(require_login),
    db: Session = Depends(get_db),
):
    faturas = (
        db.query(FaturaCartao)
        .options(joinedload(FaturaCartao.cartao))
        .order_by(FaturaCartao.data_vencimento.desc())
        .all()
    )
    return templates.TemplateResponse("faturas.html", {
        "request": request, "sessao": sessao, "faturas": faturas,
    })


@router.get("/{fatura_id}", response_class=HTMLResponse)
async def detalhe_fatura(
    fatura_id: int,
    request: Request,
    sessao=Depends(require_login),
    db: Session = Depends(get_db),
):
    fatura = (
        db.query(FaturaCartao)
        .options(joinedload(FaturaCartao.cartao))
        .filter(FaturaCartao.fatura_id == fatura_id)
        .first()
    )
    if not fatura:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/faturas")

    operacoes = (
        db.query(Operacao)
        .filter(Operacao.operacoes_fatura == fatura_id, Operacao.operacoes_validacao == 1)
        .order_by(Operacao.operacoes_data_lancamento)
        .all()
    )
    return templates.TemplateResponse("faturas_detalhes.html", {
        "request": request, "sessao": sessao,
        "fatura": fatura, "operacoes": operacoes,
    })
