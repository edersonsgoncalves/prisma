"""routers/contas.py — Gerenciamento de contas bancárias."""
from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.templates import templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.auth import require_login
from app.models import ContaBancaria, Operacao

router = APIRouter(prefix="/contas", tags=["contas"])
BASE_DIR = Path(__file__).resolve().parent.parent
# templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@router.get("", response_class=HTMLResponse)
async def listar_contas(
    request: Request,
    sessao: dict = Depends(require_login),
    db: Session = Depends(get_db),
):
    contas = db.query(ContaBancaria).all()

    # Saldo atual de cada conta (1 query GROUP BY)
    saldos = dict(
        db.query(Operacao.operacoes_conta, func.sum(Operacao.operacoes_valor))
        .filter(Operacao.operacoes_efetivado == 1, Operacao.operacoes_validacao == 1)
        .group_by(Operacao.operacoes_conta)
        .all()
    )

    return templates.TemplateResponse("contas.html", {
        "request": request, "sessao": sessao,
        "contas": contas, "saldos": saldos,
    })


@router.post("/nova")
async def nova_conta(
    nome: str = Form(...),
    tipo: int = Form(...),
    moeda: int = Form(default=1),
    limite: str = Form(default="0,00"), 
    fechamento: int = Form(default=0),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    # Trata a string do limite (ex: "1.250,50" -> "1250.50")
    try:
        limite_limpo = limite.replace('.', '').replace(',', '.')
        limite_float = float(limite_limpo)
    except (ValueError, AttributeError):
        limite_float = 0.0

    conta = ContaBancaria(
        nome_conta=nome, tipo_conta=tipo,
        conta_moeda=moeda, contas_limite=limite_float,
        contas_cartao_fechamento=fechamento,
    )
    db.add(conta); db.commit()
    return RedirectResponse(url="/contas", status_code=303)

@router.post("/editar/{conta_id}")
async def editar_conta(
    conta_id: int,
    nome: str = Form(...),
    tipo: int = Form(...),
    moeda: int = Form(default=1),
    limite: str = Form(default="0,00"),
    fechamento: int = Form(default=0),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    # Trata a string do limite (ex: "1.250,50" -> "1250.50")
    try:
        limite_limpo = limite.replace('.', '').replace(',', '.')
        limite_float = float(limite_limpo)
    except (ValueError, AttributeError):
        limite_float = 0.0
    conta = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta_id).first()
    if conta:
        conta.nome_conta = nome; conta.tipo_conta = tipo
        conta.conta_moeda = moeda; conta.contas_limite = limite_float
        conta.contas_cartao_fechamento = fechamento
        db.commit()
    return RedirectResponse(url="/contas", status_code=302)
