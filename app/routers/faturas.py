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
from sqlalchemy import extract, func, inspect,select

from app.database import get_db
from app.auth import require_login
from app.models import FaturaCartao, Operacao, ContaBancaria, Categoria, CartaoAdicional
from app.routers.lancamentos import log_evento
from app.helpers import formata_moeda_brl, mostra_data, cor_valor, mes_por_extenso, formata_parcela

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
        .options(joinedload(FaturaCartao.cartao).joinedload(ContaBancaria.adicionais))
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

    operacoes_todas = (
        db.query(Operacao)
        .options(joinedload(Operacao.adicional), joinedload(Operacao.transf_rel_obj).joinedload(Operacao.conta))
        .filter(Operacao.operacoes_fatura == fatura_id, Operacao.operacoes_validacao == 1)
        .order_by(Operacao.operacoes_data_lancamento)
        .all()
    )

    # Separação: Lançamentos normais vs Pagamentos de Fatura (Tipo 0)
    pagamentos = [op for op in operacoes_todas if op.operacoes_tipo == 0]
    operacoes = [op for op in operacoes_todas if op.operacoes_tipo != 0]
    
    # Agrupamento para a UI: Evita erro de 'NoneType' vs 'int' no Jinja2
    # Ordenamos primeiro por adicional_id (None -> 0) e depois por data
    ops_sorted = sorted(operacoes, key=lambda x: (x.operacoes_adicional_id or 0, x.operacoes_data_lancamento))
    
    from itertools import groupby
    operacoes_agrupadas = []
    for add_id, group in groupby(ops_sorted, key=lambda x: x.operacoes_adicional_id):
        operacoes_agrupadas.append((add_id, list(group)))
    
    # Precisamos das contas para o modal de transferência
    contas = db.query(ContaBancaria).order_by(ContaBancaria.nome_conta).all()
    categorias = db.query(Categoria).order_by(
        func.coalesce(Categoria.categorias_pai_id, Categoria.categorias_id),
        Categoria.categorias_pai_id.isnot(None),
        Categoria.categorias_nome
    ).all()

    # --- ENRIQUECIMENTO DE DADOS PARA A SIDEBAR ---
    stats = {
        "saldo_anterior": Decimal("0.00"),
        "total_pago": Decimal("0.00"),
        "despesas": Decimal("0.00"),
        "total_conciliado": Decimal("0.00"),
        "total_nao_conciliado": Decimal("0.00"),
        "total_fatura": Decimal("0.00"),
    }
    
    if fatura_anterior:
        f_ant = db.query(FaturaCartao).filter(FaturaCartao.fatura_id == fatura_anterior.fatura_id).first()
        if f_ant:
            stats["saldo_anterior"] = f_ant.valor_total or Decimal("0.00")

    # Calcula valor total da fatura
    stats["total_fatura"] = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_fatura == fatura_id,
        Operacao.operacoes_valor < 0,
        Operacao.operacoes_tipo !=0,
        Operacao.operacoes_validacao == 1
    ).scalar() or Decimal("0.00")

    # Soma de pagamentos (Tipos 0 e 4 para manter compatibilidade)
    stats["total_pago"] = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_fatura == fatura_id,
        Operacao.operacoes_valor > 0,
        Operacao.operacoes_tipo.in_([0, 4]),
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
        Operacao.operacoes_tipo != 0
    ).scalar() or Decimal("0.00")

    stats["total_nao_conciliado"] = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_fatura == fatura_id,
        Operacao.operacoes_efetivado == 0,
        Operacao.operacoes_tipo != 0
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

    status = ""
    if fatura.fechado == 1:
        status = "Fatura fechada"
    else: 
        if fatura.data_vencimento < date.today():
            diff_dias = (date.today() - fatura.data_vencimento).days
            status = f"Vencido há {diff_dias} dias"
        elif fatura.data_vencimento > date.today():
            diff_dias = (fatura.data_vencimento - date.today()).days
            status = f"Vence em {diff_dias} dias"
        elif fatura.data_vencimento == date.today():
            status = "Vencimento hoje"
    
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
        "mes_venc_pt": mes_venc_pt,
        "faturas_todas": faturas_do_cartao,
        "operacoes_agrupadas": operacoes_agrupadas,
        "pagamentos": pagamentos,
        "status": status,
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

    # Verifica se há operações não conciliadas
    operacoes_nao_conciliadas = db.query(func.count(Operacao.operacoes_id)).filter(
        Operacao.operacoes_fatura == fatura_id,
        Operacao.operacoes_efetivado == 0,
        Operacao.operacoes_tipo != 0
    ).scalar()

    if operacoes_nao_conciliadas:
        return RedirectResponse(
            url=f"/faturas/{fatura_id}?erro=conciliacao", 
            status_code=303
        )

    # Se data_vencimento for None, usamos a data de hoje como base
    base_venc = fatura.data_vencimento or date.today()
    
    # Se data_fechamento for None, podemos assumir 7 dias antes do vencimento 
    # ou usar a data de hoje. Aqui usaremos hoje como segurança.
    base_fech = fatura.data_fechamento or (base_venc - relativedelta(days=7))

    # Agora a soma nunca falhará
    proxima_venc = base_venc + relativedelta(months=1)
    proxima_fech = base_fech + relativedelta(months=1)

    # Atualiza valores da fatura atual
    valor_total = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_fatura == fatura_id,
        Operacao.operacoes_efetivado == 1,
        Operacao.operacoes_tipo != 0
    ).scalar() or Decimal("0.00")

    fatura.valor_total = valor_total

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
    
        fatura_id_final = nova_fatura.fatura_id  # Captura o ID recém-criado
    else:
        fatura_id_final = existe.fatura_id 

    cartao_adicional = db.query(CartaoAdicional).filter(
        CartaoAdicional.conta_id == fatura.conta_id
    ).first()

    cartao_adicional_nome = cartao_adicional.apelido if cartao_adicional else "Sem Nome"


    # Gerar lançamento de pagamento para próxima fatura
    op_pagamento_saida = Operacao(
        operacoes_data_lancamento=base_venc,
        operacoes_valor=-abs(valor_total),
        operacoes_tipo=0,
        operacoes_validacao=1,
        operacoes_efetivado=0,
        operacoes_conta=fatura.cartao.contas_prev_debito,
        operacoes_descricao=f"Pagamento fatura | {cartao_adicional_nome} | Fatura {MESES_PT[fatura.mes_referencia.month]}/{fatura.mes_referencia.strftime('%y')}"
    )
    db.add(op_pagamento_saida)
    db.flush()
    op_pagamento_entrada = Operacao(
        operacoes_data_lancamento=base_venc,
        operacoes_valor=abs(valor_total),
        operacoes_tipo=0,
        operacoes_transf_rel=op_pagamento_saida.operacoes_id,
        operacoes_validacao=1,
        operacoes_efetivado=0,
        operacoes_fatura=fatura_id_final,
        operacoes_conta=fatura.conta_id,
        operacoes_descricao=f"Pagamento fatura | {cartao_adicional_nome} | Fatura {MESES_PT[fatura.mes_referencia.month]}/{fatura.mes_referencia.strftime('%y')}"
    )
    db.add(op_pagamento_entrada)
    db.flush()
    op_pagamento_saida.operacoes_transf_rel = op_pagamento_entrada.operacoes_id

    fatura.fechado = 1
    
    
    log_evento(db, "UPDATE", "FATURA", fatura.fatura_id, 
               f"Fatura fechada no valor de {formata_moeda_brl(abs(valor_total))}. Próxima fatura com vencimento em {mostra_data(proxima_venc)}", 
               sessao.get("id"))
    
    db.commit()

    return RedirectResponse(url=f"/faturas/{nova_fatura.fatura_id}", status_code=303)


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
        operacoes_fatura=fatura.fatura_id, # Vincula à fatura
        operacoes_tipo=0, # Pagamento de Fatura (TipoEspecial)
        operacoes_efetivado=1,
        operacoes_data_efetivado=datetime.now(),
        operacoes_validacao=1
    )

    db.add(op_saida)
    db.add(op_entrada)
    db.flush()

    op_saida.operacoes_transf_rel = op_entrada.operacoes_id
    op_entrada.operacoes_transf_rel = op_saida.operacoes_id

    log_evento(db, "INSERT", "PAGAMENTO_FATURA", op_saida.operacoes_id, f"Pagamento de {formata_moeda_brl(valor_float)} para fatura {fatura.fatura_id}", sessao.get("id"))
    db.commit()

    return RedirectResponse(url=f"/faturas/{fatura.fatura_id}", status_code=303)


@router.post("/mover-lancamento")
async def mover_lancamento_fatura(
    operacao_id: int = Form(...),
    nova_fatura_id: int = Form(...),
    db: Session = Depends(get_db),
    sessao=Depends(require_login),
):
    op = db.query(Operacao).filter(Operacao.operacoes_id == operacao_id).first()
    if not op:
        return RedirectResponse(url="/faturas", status_code=303)
    
    id_antiga = op.operacoes_fatura
    op.operacoes_fatura = nova_fatura_id
    
    log_evento(db, "UPDATE", "OPERACAO", operacao_id, f"Movido da fatura {id_antiga} para {nova_fatura_id}", sessao.get("id"))
    db.commit()
    
    return RedirectResponse(url=f"/faturas/{nova_fatura_id}", status_code=303)


@router.post("/converter-transferencia")
async def converter_em_transferencia(
    operacao_id: int = Form(...),
    conta_destino_id: int = Form(...),
    db: Session = Depends(get_db),
    sessao=Depends(require_login),
):
    op = db.query(Operacao).filter(Operacao.operacoes_id == operacao_id).first()
    if not op or op.operacoes_transf_rel:
        return RedirectResponse(url="/faturas", status_code=303)

    # 1. Atualiza a operação original para ser o lado Saída da Transferência (tipo 4)
    op.operacoes_tipo = "4"
    
    # 2. Cria a operação correspondente (Entrada) na conta de destino
    import uuid
    grupo = f"TRF-{uuid.uuid4().hex[:10]}"
    op.operacoes_grupo_id = grupo

    nova_op = Operacao(
        operacoes_data_lancamento=op.operacoes_data_lancamento,
        operacoes_descricao=op.operacoes_descricao,
        operacoes_conta=conta_destino_id,
        operacoes_valor=abs(op.operacoes_valor), # Entrada (valor positivo)
        operacoes_tipo="4", # Transferência
        operacoes_efetivado=1,
        operacoes_data_efetivado=datetime.now(),
        operacoes_validacao=1,
        operacoes_transf_rel=op.operacoes_id,
        operacoes_grupo_id=grupo
    )
    db.add(nova_op)
    db.flush()
    
    op.operacoes_transf_rel = nova_op.operacoes_id
    
    log_evento(db, "INSERT", "TRANSFERENCIA", nova_op.operacoes_id, f"Convertido de despesa#{op.operacoes_id} para transferência", sessao.get("id"))
    db.commit()
    
    return RedirectResponse(url=f"/faturas/{op.operacoes_fatura}", status_code=303)
