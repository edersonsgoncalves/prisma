from datetime import date, datetime, timedelta
from typing import Optional
import uuid
from dateutil.relativedelta import relativedelta

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login
from app.models import Operacao, ContaBancaria
from .lancamentos_utils import log_evento, get_or_create_fatura, recalcular_total_fatura

router = APIRouter(prefix="/lancamentos", tags=["lancamentos"])

@router.post("/transferir")
async def inserir_transferencia(
    request: Request,
    descricao: str = Form(...),
    conta: int = Form(...),
    conta_destino: int = Form(...),
    valor: str = Form(...),
    data: str = Form(...),
    fatura: Optional[str] = Form(default=None),
    parcela_atual: Optional[int] = Form(default=None),
    parcela_total: Optional[int] = Form(default=None),
    next_url: Optional[str] = Form(default=None),
    efetivado: int = Form(default=0),
    data_efetivado: Optional[str] = Form(default=None),
    modo_repeticao_modern_transf: Optional[str] = Form(default="unica"),
    num_parcelas: Optional[str] = Form(default=None),
    parcela_inicial: Optional[str] = Form(default=None),
    frequencia: Optional[str] = Form(default="mensal"),
    intervalo: Optional[str] = Form(default=None),
    valor_referencia: Optional[str] = Form(default=None),
    is_valor_parcela: Optional[str] = Form(default=None),
    adicional_id: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    conta_origem_obj = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta).first()
    conta_destino_obj = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta_destino).first()

    add_id = int(adicional_id) if adicional_id and adicional_id.strip() else None
    num_parcelas_int = int(num_parcelas) if num_parcelas and num_parcelas.strip() else 2
    parcela_inicial_int = int(parcela_inicial) if parcela_inicial and parcela_inicial.strip() else 1
    intervalo_int = int(intervalo) if intervalo and intervalo.strip() else 1

    valor_limpo = valor.replace(".", "").replace(",", ".")
    valor_float = float(valor_limpo)

    modo = modo_repeticao_modern_transf or "unica"
    is_repetir = modo in ("parcelada", "fixa")

    repeticoes = 1
    if is_repetir:
        if modo == "parcelada":
            repeticoes = (num_parcelas_int - parcela_inicial_int) + 1
        else:
            repeticoes = 24

    valor_ref_limpo = valor_referencia.replace(".", "").replace(",", ".") if valor_referencia and valor_referencia.strip() else None
    eh_valor_parcela = is_valor_parcela == "on"

    if is_repetir and valor_ref_limpo and not eh_valor_parcela:
        total = float(valor_ref_limpo)
        valor_unitario = round(total / repeticoes, 2)
        resto_divisao = total - (valor_unitario * repeticoes)
    elif is_repetir and valor_ref_limpo and eh_valor_parcela:
        valor_unitario = float(valor_ref_limpo)
        resto_divisao = 0
    else:
        valor_unitario = valor_float
        resto_divisao = 0

    fat_id = int(fatura) if fatura and fatura.strip() else None

    parcela_str = None
    if parcela_atual and parcela_total:
        parcela_str = f"{parcela_atual:03d}.{parcela_total:03d}"

    dt_operacao = date.fromisoformat(data)

    dt_efetivado = None
    if efetivado:
        dt_efetivado = datetime.fromisoformat(data_efetivado) if data_efetivado else datetime.now()

    grupo_id = f"TRF-{uuid.uuid4().hex[:10]}"
    faturas_para_recalcular = set()

    for i in range(repeticoes):
        curr_dt = dt_operacao
        if i > 0:
            if modo == "parcelada" or frequencia == "mensal":
                curr_dt = dt_operacao + relativedelta(months=i * intervalo_int)
            elif frequencia == "semanal":
                curr_dt = dt_operacao + timedelta(weeks=i * intervalo_int)
            elif frequencia == "anual":
                curr_dt = dt_operacao + relativedelta(years=i * intervalo_int)

        curr_fat_saida = None
        curr_fat_entrada = None

        if conta_origem_obj and conta_origem_obj.tipo_conta == 4:
            curr_fat_saida = fat_id if i == 0 else get_or_create_fatura(db, conta, curr_dt)

        if conta_destino_obj and conta_destino_obj.tipo_conta == 4:
            curr_fat_entrada = get_or_create_fatura(db, conta_destino, curr_dt)

        curr_valor_f = valor_unitario
        if i == repeticoes - 1:
            curr_valor_f += resto_divisao

        curr_parcela = None
        if is_repetir and modo == "parcelada":
            num_atual = parcela_inicial_int + i
            curr_parcela = f"{num_atual:03d}.{num_parcelas_int:03d}"
        elif parcela_str:
            curr_parcela = parcela_str

        curr_efetivado = efetivado if i == 0 else 0
        curr_dt_efetivado = dt_efetivado if i == 0 else None

        op_saida = Operacao(
            operacoes_data_lancamento=curr_dt,
            operacoes_descricao=descricao,
            operacoes_conta=conta,
            operacoes_valor=-abs(curr_valor_f),
            operacoes_tipo="4",
            operacoes_fatura=curr_fat_saida,
            operacoes_parcela=curr_parcela,
            operacoes_efetivado=curr_efetivado,
            operacoes_data_efetivado=curr_dt_efetivado,
            operacoes_validacao=1,
            operacoes_grupo_id=grupo_id,
            operacoes_adicional_id=add_id,
        )
        op_entrada = Operacao(
            operacoes_data_lancamento=curr_dt,
            operacoes_descricao=descricao,
            operacoes_conta=conta_destino,
            operacoes_valor=abs(curr_valor_f),
            operacoes_tipo="4",
            operacoes_fatura=curr_fat_entrada,
            operacoes_parcela=curr_parcela,
            operacoes_efetivado=curr_efetivado,
            operacoes_data_efetivado=curr_dt_efetivado,
            operacoes_validacao=1,
            operacoes_grupo_id=grupo_id,
            operacoes_adicional_id=add_id,
        )

        db.add(op_saida)
        db.add(op_entrada)
        db.flush()

        op_saida.operacoes_transf_rel = op_entrada.operacoes_id
        op_entrada.operacoes_transf_rel = op_saida.operacoes_id

        if curr_fat_saida: faturas_para_recalcular.add(curr_fat_saida)
        if curr_fat_entrada: faturas_para_recalcular.add(curr_fat_entrada)

        if i == 0:
            log_evento(db, "INSERT", "TRANSFERÊNCIA", op_saida.operacoes_id,
                       f"Série '{descricao}' ({repeticoes}x)", sessao.get("id"))

    db.commit()

    for f_id in faturas_para_recalcular:
        recalcular_total_fatura(db, f_id)

    redirecionar = next_url or request.headers.get("referer") or f"/extrato?c={conta}"
    return RedirectResponse(url=redirecionar, status_code=303)

@router.post("/editar-transferencia/{op_id}")
async def editar_transferencia(
    request: Request,
    op_id: int,
    descricao: str = Form(...),
    conta: int = Form(alias="conta_origem"),
    conta_destino: int = Form(...),
    valor: str = Form(...),
    data: str = Form(...),
    efetivado: int = Form(default=0),
    data_efetivado: Optional[str] = Form(default=None),
    adicional_id: Optional[int] = Form(default=None),
    escopo: str = Form(default="so_este"),
    next_url: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    """Edita o par de operações de uma transferência (saída + entrada espelho)."""
    op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if not op:
        redirecionar = next_url or request.headers.get("referer") or "/dashboard"
        return RedirectResponse(url=redirecionar, status_code=303)

    op_saida = op if float(op.operacoes_valor) < 0 else None
    op_entrada = op if float(op.operacoes_valor) > 0 else None

    if op.operacoes_transf_rel:
        op_rel = db.query(Operacao).filter(Operacao.operacoes_id == op.operacoes_transf_rel).first()
        if op_rel:
            if float(op.operacoes_valor) < 0:
                op_entrada = op_rel
            else:
                op_saida = op_rel

    valor_limpo = valor.replace(".", "").replace(",", ".")
    valor_float = float(valor_limpo)
    dt_operacao = date.fromisoformat(data)
    
    # Lógica de Efetivação
    dt_efetivado_val = None
    if efetivado:
        dt_efetivado_val = datetime.fromisoformat(data_efetivado) if data_efetivado else datetime.now()

    # Captura faturas antigas para recalcular depois
    faturas_para_recalcular = set()
    if op_saida and op_saida.operacoes_fatura: faturas_para_recalcular.add(op_saida.operacoes_fatura)
    if op_entrada and op_entrada.operacoes_fatura: faturas_para_recalcular.add(op_entrada.operacoes_fatura)

    old_date = op.operacoes_data_lancamento
    grupo_id = op.operacoes_grupo_id

    detalhes = f"Transferência editada: '{descricao}' R$ {valor_float} de conta {conta} -> {conta_destino}"

    if op_saida:
        op_saida.operacoes_descricao = descricao
        op_saida.operacoes_conta = conta
        op_saida.operacoes_valor = -abs(valor_float)
        op_saida.operacoes_data_lancamento = dt_operacao
        op_saida.operacoes_adicional_id = adicional_id
        op_saida.operacoes_efetivado = efetivado
        op_saida.operacoes_data_efetivado = dt_efetivado_val
        
        # Recalcula fatura se a conta de origem for cartão
        acct_saida_obj = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta).first()
        if acct_saida_obj and acct_saida_obj.tipo_conta == 4:
            op_saida.operacoes_fatura = get_or_create_fatura(db, conta, dt_operacao)
            if op_saida.operacoes_fatura: faturas_para_recalcular.add(op_saida.operacoes_fatura)
        else:
            op_saida.operacoes_fatura = None

        log_evento(db, "UPDATE", "OPERACAO", op_saida.operacoes_id, detalhes, sessao.get("id"))

    if op_entrada:
        op_entrada.operacoes_descricao = descricao
        op_entrada.operacoes_conta = conta_destino
        op_entrada.operacoes_valor = abs(valor_float)
        op_entrada.operacoes_data_lancamento = dt_operacao
        op_entrada.operacoes_efetivado = efetivado
        op_entrada.operacoes_data_efetivado = dt_efetivado_val

        # Recalcula fatura se a conta destino for cartão
        acct_entrada_obj = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta_destino).first()
        if acct_entrada_obj and acct_entrada_obj.tipo_conta == 4:
            op_entrada.operacoes_fatura = get_or_create_fatura(db, conta_destino, dt_operacao)
            if op_entrada.operacoes_fatura: faturas_para_recalcular.add(op_entrada.operacoes_fatura)
        else:
            op_entrada.operacoes_fatura = None

        log_evento(db, "UPDATE", "OPERACAO", op_entrada.operacoes_id, detalhes, sessao.get("id"))

    if escopo == "subsequentes":
        new_date = dt_operacao
        ops_seguintes = []
        
        if grupo_id:
            ops_seguintes = db.query(Operacao).filter(
                Operacao.operacoes_grupo_id == grupo_id,
                Operacao.operacoes_data_lancamento > old_date,
                Operacao.operacoes_validacao == 1
            ).order_by(Operacao.operacoes_data_lancamento).all()
        else:
            ops_seguintes = db.query(Operacao).filter(
                Operacao.operacoes_descricao == op.operacoes_descricao,
                Operacao.operacoes_data_lancamento > old_date,
                Operacao.operacoes_validacao == 1
            ).all()

        for s_op in ops_seguintes:
            s_op.operacoes_descricao = descricao
            
            if new_date != old_date:
                diff = relativedelta(s_op.operacoes_data_lancamento, old_date)
                new_s_dt = new_date + diff
                s_op.operacoes_data_lancamento = new_s_dt
            
            if float(s_op.operacoes_valor) < 0:
                s_op.operacoes_valor = -abs(valor_float)
                s_op.operacoes_conta = conta
                # Trata fatura em cascata
                acct_s = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta).first()
                if acct_s and acct_s.tipo_conta == 4:
                    old_f = s_op.operacoes_fatura
                    if old_f: faturas_para_recalcular.add(old_f)
                    s_op.operacoes_fatura = get_or_create_fatura(db, conta, s_op.operacoes_data_lancamento)
                    if s_op.operacoes_fatura: faturas_para_recalcular.add(s_op.operacoes_fatura)
                else:
                    if s_op.operacoes_fatura: faturas_para_recalcular.add(s_op.operacoes_fatura)
                    s_op.operacoes_fatura = None
            else:
                s_op.operacoes_valor = abs(valor_float)
                s_op.operacoes_conta = conta_destino
                # Trata fatura em cascata
                acct_s = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta_destino).first()
                if acct_s and acct_s.tipo_conta == 4:
                    old_f = s_op.operacoes_fatura
                    if old_f: faturas_para_recalcular.add(old_f)
                    s_op.operacoes_fatura = get_or_create_fatura(db, conta_destino, s_op.operacoes_data_lancamento)
                    if s_op.operacoes_fatura: faturas_para_recalcular.add(s_op.operacoes_fatura)
                else:
                    if s_op.operacoes_fatura: faturas_para_recalcular.add(s_op.operacoes_fatura)
                    s_op.operacoes_fatura = None
            
            if not s_op.operacoes_grupo_id:
                 if not op.operacoes_grupo_id:
                      op.operacoes_grupo_id = f"TRF-{uuid.uuid4().hex[:10]}"
                 s_op.operacoes_grupo_id = op.operacoes_grupo_id

            log_evento(db, "UPDATE", "OPERACAO", s_op.operacoes_id,
                       f"Transferência editada em cascata: '{descricao}'", sessao.get("id"))

    db.commit()

    # Recalcula as faturas após o commit
    for f_id in faturas_para_recalcular:
        if f_id: recalcular_total_fatura(db, f_id)

    redirecionar = next_url or request.headers.get("referer") or "/dashboard"
    return RedirectResponse(url=redirecionar, status_code=303)
