"""routers/contas.py — Gerenciamento de contas bancárias."""
from pathlib import Path
from datetime import date
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.templates import templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from app.database import get_db
from app.auth import require_login
from app.models import ContaBancaria, Operacao, ContasMoeda, TipoConta

router = APIRouter(prefix="/contas", tags=["contas"])
BASE_DIR = Path(__file__).resolve().parent.parent
# templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@router.get("", response_class=HTMLResponse)
async def listar_contas(
    request: Request,
    sessao: dict = Depends(require_login),
    db: Session = Depends(get_db),
):
    contas = db.query(ContaBancaria).options(joinedload(ContaBancaria.moeda_rel)).all()

    # Saldo atual de cada conta (1 query GROUP BY)
    saldos = dict(
        db.query(Operacao.operacoes_conta, func.sum(Operacao.operacoes_valor))
        .filter(Operacao.operacoes_efetivado == 1, Operacao.operacoes_validacao == 1)
        .group_by(Operacao.operacoes_conta)
        .all()
    )

    # Busca moedas e tipos
    moedas = db.query(ContasMoeda).all()
    tipos = db.query(TipoConta).filter(TipoConta.idtipos_contas != 4).all()

    return templates.TemplateResponse("contas.html", {
        "request": request, "sessao": sessao,
        "contas": contas, "saldos": saldos,
        "moedas": moedas, "tipos": tipos,
        "hoje": date.today().isoformat(),
    })


@router.post("/nova")
async def nova_conta(
    nome: str = Form(...),
    tipo: int = Form(...),
    conta_moeda: int = Form(default=1),
    limite: str = Form(default="0,00"), 
    fechamento: int = Form(default=0),
    conta_saldo_inicial: str = Form(default="0,00"),
    data_conta_saldo_incial: str = Form(default=None),
    contas_desconsiderar_saldo: str = Form(default=None),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    # Trata strings de valores (ex: "1.250,50" -> 1250.50)
    def clean_float(val: str) -> float:
        try:
            return float(val.replace('.', '').replace(',', '.'))
        except (ValueError, AttributeError):
            return 0.0

    limite_float = clean_float(limite)
    saldo_inicial_float = clean_float(conta_saldo_inicial)
    
    # Checkbox vem como "on" ou None
    desconsiderar = 1 if contas_desconsiderar_saldo == "on" else 0

    conta = ContaBancaria(
        nome_conta=nome, tipo_conta=tipo,
        conta_moeda=conta_moeda, contas_limite=limite_float,
        contas_cartao_fechamento=fechamento,
        contas_desconsiderar_saldo=desconsiderar
    )
    db.add(conta); db.flush() # flush para pegar o ID antes do commit

    # Se houver saldo inicial, cria uma operação
    if saldo_inicial_float != 0:
        dt_saldo = date.fromisoformat(data_conta_saldo_incial) if data_conta_saldo_incial else date.today()
        op = Operacao(
            operacoes_data_lancamento=dt_saldo,
            operacoes_descricao="Saldo Inicial",
            operacoes_conta=conta.conta_id,
            operacoes_valor=saldo_inicial_float,
            # Se for positivo (entrada) é tipo 1 (Receita), se negativo (saída) é tipo 3 (Despesa)
            operacoes_tipo=1 if saldo_inicial_float > 0 else 3,
            operacoes_efetivado=1,
            operacoes_validacao=1
        )
        db.add(op)

    db.commit()
    return RedirectResponse(url="/contas", status_code=303)

@router.post("/editar/{conta_id}")
async def editar_conta(
    conta_id: int,
    nome: str = Form(...),
    tipo: int = Form(...),
    conta_moeda: int = Form(default=1),
    limite: str = Form(default="0,00"),
    fechamento: int = Form(default=0),
    contas_desconsiderar_saldo: str = Form(default=None),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    # Trata a string do limite
    try:
        limite_limpo = limite.replace('.', '').replace(',', '.')
        limite_float = float(limite_limpo)
    except (ValueError, AttributeError):
        limite_float = 0.0

    conta = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta_id).first()
    if conta:
        conta.nome_conta = nome; conta.tipo_conta = tipo
        conta.conta_moeda = conta_moeda; conta.contas_limite = limite_float
        conta.contas_cartao_fechamento = fechamento
        conta.contas_desconsiderar_saldo = 1 if contas_desconsiderar_saldo == "on" else 0
        db.commit()
    return RedirectResponse(url="/contas", status_code=302)
