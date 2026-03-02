"""routers/faturas.py — Faturas de cartão de crédito."""
from decimal import Decimal
from pathlib import Path
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Request, Depends, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.templates import templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import extract, func

from app.database import get_db
from app.auth import require_login
from app.models import FaturaCartao, Operacao, ContaBancaria, Categoria
from app.routers.lancamentos import log_evento

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

# --- LÓGICA DE NAVEGAÇÃO ENTRE FATURAS ---
    # No seu modelo, o campo é 'conta_id'
    id_do_cartao = fatura.conta_id
    data_ref = fatura.data_vencimento

    # 1. Fatura Anterior (A mais próxima antes da atual)
    fatura_anterior = (
        db.query(FaturaCartao.fatura_id)
        .filter(FaturaCartao.conta_id == id_do_cartao, 
                FaturaCartao.data_vencimento < data_ref)
        .order_by(FaturaCartao.data_vencimento.desc())
        .first()
    )

    # 2. Próxima Fatura (A mais próxima depois da atual)
    proxima_fatura = (
        db.query(FaturaCartao.fatura_id)
        .filter(FaturaCartao.conta_id == id_do_cartao, 
                FaturaCartao.data_vencimento > data_ref)
        .order_by(FaturaCartao.data_vencimento.asc())
        .first()
    )

    # 3. Fatura "Lógica" Atual (Baseada na data de hoje)
    from datetime import date
    hoje = date.today()
    inicio_mes = hoje.replace(day=1)
    
    id_atual_logic = (
        db.query(FaturaCartao.fatura_id)
        .filter(
            FaturaCartao.conta_id == id_do_cartao,
            FaturaCartao.data_vencimento >= inicio_mes,
            FaturaCartao.fechado == 0  # No seu modelo é SmallInteger, usamos 0 para aberta
        )
        .order_by(FaturaCartao.data_vencimento.asc())
        .first()
    )

    # Se não houver uma aberta para este mês, pegamos a última fatura existente desse cartão
    if not id_atual_logic:
        id_atual_logic = (
            db.query(FaturaCartao.fatura_id)
            .filter(FaturaCartao.conta_id == id_do_cartao)
            .order_by(FaturaCartao.data_vencimento.desc())
            .first()
        )
    # --- FIM DA LÓGICA DE NAVEGAÇÃO ---

    operacoes = (
        db.query(Operacao)
        .filter(Operacao.operacoes_fatura == fatura_id, Operacao.operacoes_validacao == 1)
        .order_by(Operacao.operacoes_data_lancamento)
        .all()
    )
    contas = db.query(ContaBancaria).order_by(ContaBancaria.nome_conta).all()
    categorias = db.query(Categoria).order_by(Categoria.categorias_nome).all()

    return templates.TemplateResponse("faturas_detalhes.html", {
        "request": request, 
        "sessao": sessao,
        "fatura": fatura, 
        "operacoes": operacoes,
        "contas_todas": contas, 
        "categorias": categorias,
        "id_anterior": fatura_anterior.fatura_id if fatura_anterior else None,
        "id_proxima": proxima_fatura.fatura_id if proxima_fatura else None,
        "id_atual_logica": id_atual_logic.fatura_id if id_atual_logic else None
    })

@router.get("/{fatura_id}/fechar")
async def fechar_fatura(
    fatura_id: int,
    db: Session = Depends(get_db),
    sessao=Depends(require_login),
):
    fatura = db.query(FaturaCartao).filter(FaturaCartao.fatura_id == fatura_id).first()
    if not fatura or fatura.fechado:
        return RedirectResponse(url=f"/faturas/{fatura_id}", status_code=303)

    # 1. Fecha a fatura atual
    fatura.fechado = 1
    
    # 2. Cria a próxima fatura
    proxima_venc = fatura.data_vencimento + relativedelta(months=1)
    proxima_fech = fatura.data_fechamento + relativedelta(months=1)
    
    # Verifica se já existe para não duplicar
    existe = db.query(FaturaCartao).filter(
        FaturaCartao.conta_id == fatura.conta_id,
        FaturaCartao.data_vencimento == proxima_venc
    ).first()

    if not existe:
        nova_fatura = FaturaCartao(
            conta_id=fatura.conta_id,
            data_vencimento=proxima_venc,
            data_fechamento=proxima_fech,
            fechado=0,
            valor_total=0
        )
        db.add(nova_fatura)
    
    log_evento(db, "UPDATE", "FATURA", fatura.fatura_id, f"Fatura fechada. Próxima em {proxima_venc}", sessao.get("id"))
    db.commit()
    
    return RedirectResponse(url=f"/faturas/{fatura_id}", status_code=303)


@router.post("/pagar")
async def pagar_fatura(
    request: Request,
    descricao: str = Form(...),
    valor: str = Form(...),
    data: str = Form(...),
    conta_origem: int = Form(...),
    conta_destino_fatura: int = Form(...), # ID da FATURA, mas conta_destino real é o cartão
    db: Session = Depends(get_db),
    sessao=Depends(require_login),
):
    fatura = db.query(FaturaCartao).filter(FaturaCartao.fatura_id == conta_destino_fatura).first()
    if not fatura:
        return JSONResponse(status_code=404, content={"erro": "Fatura não encontrada"})

    # Limpa formatação BRL
    valor_limpo = valor.replace(".", "").replace(",", ".")
    valor_float = float(valor_limpo)

    dt_operacao = date.fromisoformat(data)
    
    # A conta_destino da operação é o ID da conta do cartão (fatura.conta_id)
    conta_cartao_id = fatura.conta_id

    # Criamos a transferência (Pagamento da Fatura)
    # 1. Saída da conta bancária
    op_saida = Operacao(
        operacoes_data_lancamento=dt_operacao,
        operacoes_descricao=descricao,
        operacoes_conta=conta_origem,
        operacoes_valor=-valor_float,
        operacoes_tipo="4", # Transferência
        operacoes_efetivado=1,
        operacoes_data_efetivado=datetime.now(),
        operacoes_validacao=1
    )
    
    # 2. Entrada no cartão (reduz saldo devedor)
    op_entrada = Operacao(
        operacoes_data_lancamento=dt_operacao,
        operacoes_descricao=descricao,
        operacoes_conta=conta_cartao_id,
        operacoes_valor=valor_float,
        operacoes_tipo="4", # Transferência
        operacoes_fatura=fatura.fatura_id, # Vincula à fatura
        operacoes_efetivado=1,
        operacoes_data_efetivado=datetime.now(),
        operacoes_validacao=1
    )

    db.add(op_saida)
    db.add(op_entrada)
    db.flush()

    op_saida.operacoes_transf_rel = op_entrada.operacoes_id
    op_entrada.operacoes_transf_rel = op_saida.operacoes_id

    # Atualiza saldo da fatura
    if fatura.valor_total is None:
        fatura.valor_total = Decimal("0.00")
    fatura.valor_total -= Decimal(str(valor_float))

    log_evento(db, "INSERT", "PAGAMENTO_FATURA", op_saida.operacoes_id, f"Pagamento de {valor_float} para fatura {fatura.fatura_id}", sessao.get("id"))
    db.commit()

    return RedirectResponse(url=f"/faturas/{fatura.fatura_id}", status_code=303)
