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
from sqlalchemy import extract, func, inspect

from app.database import get_db
from app.auth import require_login
from app.models import FaturaCartao, Operacao, ContaBancaria, Categoria
from app.routers.lancamentos import log_evento

router = APIRouter(prefix="/faturas", tags=["faturas"])
BASE_DIR = Path(__file__).resolve().parent.parent
# templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

MESES_PT = {
    1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr',
    5: 'Mai', 6: 'Jun', 7: 'Jul', 8: 'Ago',
    9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'
}

MESES_PT_COMPLETO = {
    1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril',
    5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
    9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
}


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

    # --- ÍNDICE DE MESES ---
    faturas_do_cartao = (
        db.query(FaturaCartao)
        .filter(FaturaCartao.conta_id == id_do_cartao)
        .order_by(FaturaCartao.data_vencimento.asc())
        .all()
    )
    
    indice_faturas = []
    for f in faturas_do_cartao:
        if f.data_vencimento:
            indice_faturas.append({
                "id": f.fatura_id,
                "mes": f.data_vencimento.month,
                "ano": f.data_vencimento.year,
                "nome_abreviado": MESES_PT[f.data_vencimento.month],
                "fechado": f.fechado
            })
    
    # Mês formatado em PT
    mes_venc_pt = ""
    if fatura.data_vencimento:
        mes_venc_pt = f"{MESES_PT_COMPLETO[fatura.data_vencimento.month]} {fatura.data_vencimento.year}"

    operacoes = (
        db.query(Operacao)
        .filter(Operacao.operacoes_fatura == fatura_id, Operacao.operacoes_validacao == 1)
        .order_by(Operacao.operacoes_data_lancamento)
        .all()
    )
    contas = db.query(ContaBancaria).order_by(ContaBancaria.nome_conta).all()
    categorias = db.query(Categoria).order_by(Categoria.categorias_nome).all()

    # --- ENRIQUECIMENTO DE DADOS PARA A SIDEBAR ---
    stats = {
        "saldo_anterior": Decimal("0.00"),
        "total_pago": Decimal("0.00"),
        "despesas": Decimal("0.00"),
        "total_conciliado": Decimal("0.00"),
        "total_nao_conciliado": Decimal("0.00"),
    }
    
    if fatura_anterior:
        f_ant = db.query(FaturaCartao).filter(FaturaCartao.fatura_id == fatura_anterior.fatura_id).first()
        if f_ant:
            stats["saldo_anterior"] = f_ant.valor_total or Decimal("0.00")

    # Soma de pagamentos (entradas positivas via transferência na fatura)
    stats["total_pago"] = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_fatura == fatura_id,
        Operacao.operacoes_valor > 0,
        Operacao.operacoes_tipo == "4",
        Operacao.operacoes_validacao == 1
    ).scalar() or Decimal("0.00")

    # Soma de despesas (valores negativos)
    stats["despesas"] = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_fatura == fatura_id,
        Operacao.operacoes_valor < 0,
        Operacao.operacoes_validacao == 1
    ).scalar() or Decimal("0.00")

    # Estatísticas de Conciliação
    stats["total_conciliado"] = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_fatura == fatura_id,
        Operacao.operacoes_efetivado == 1,
        Operacao.operacoes_validacao == 1
    ).scalar() or Decimal("0.00")

    stats["total_nao_conciliado"] = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_fatura == fatura_id,
        Operacao.operacoes_efetivado == 0,
        Operacao.operacoes_validacao == 1
    ).scalar() or Decimal("0.00")

    # Informações de Limite
    limite = fatura.cartao.contas_limite or Decimal("0.00")
    # Utilizado: saldo atual do cartão (soma de todas as operações válidas na conta do cartão)
    utilizado = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_conta == id_do_cartao,
        Operacao.operacoes_validacao == 1
    ).scalar() or Decimal("0.00")
    
    utilizado_abs = abs(utilizado)
    limite_info = {
        "limite": limite,
        "utilizado": utilizado_abs,
        "disponivel": limite - utilizado_abs
    }
    # -----------------------------------------------

    return templates.TemplateResponse("faturas_detalhes.html", {
        "request": request, 
        "sessao": sessao,
        "fatura": fatura, 
        "operacoes": operacoes,
        "contas_todas": contas, 
        "categorias": categorias,
        "id_anterior": fatura_anterior.fatura_id if fatura_anterior else None,
        "id_proxima": proxima_fatura.fatura_id if proxima_fatura else None,
        "id_atual_logica": id_atual_logic.fatura_id if id_atual_logic else None,
        "stats": stats,
        "limite_info": limite_info,
        "indice_faturas": indice_faturas,
        "mes_venc_pt": mes_venc_pt
    })

@router.get("/{fatura_id}/fechar")
async def fechar_fatura(
    fatura_id: int,
    db: Session = Depends(get_db),
    sessao=Depends(require_login),
):
    fatura = db.query(FaturaCartao).filter(FaturaCartao.fatura_id == fatura_id).first()
    
    # Validação inicial
    if not fatura or fatura.fechado == 1:
        return RedirectResponse(url=f"/faturas/{fatura_id}", status_code=303)

    # --- RESOLUÇÃO DO PROBLEMA ---
    # Se data_vencimento for None, usamos a data de hoje como base
    base_venc = fatura.data_vencimento or date.today()
    
    # Se data_fechamento for None, podemos assumir 7 dias antes do vencimento 
    # ou usar a data de hoje. Aqui usaremos hoje como segurança.
    base_fech = fatura.data_fechamento or (base_venc - relativedelta(days=7))

    # Agora a soma nunca falhará
    proxima_venc = base_venc + relativedelta(months=1)
    proxima_fech = base_fech + relativedelta(months=1)
    # -----------------------------

    # 1. Marca como fechada
    fatura.fechado = 1
    
    # 2. Verifica se a próxima fatura já existe para esse cartão
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
            valor_total=0,
            mes_referencia=proxima_venc.replace(day=1) # Recomendado preencher este campo
        )
        db.add(nova_fatura)
        db.flush() # Garante que a nova_fatura ganhe um ID antes do log
    
    log_evento(db, "UPDATE", "FATURA", fatura.fatura_id, 
               f"Fatura fechada. Próxima gerada para {proxima_venc}", 
               sessao.get("id"))
    
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
