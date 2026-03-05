from datetime import date, datetime, timedelta
from typing import Optional
from calendar import monthrange
import uuid
from dateutil.relativedelta import relativedelta
from decimal import Decimal
import json

from fastapi import APIRouter, Request, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.encoders import jsonable_encoder

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login
from app.models import Operacao, ContaBancaria, Categoria, FaturaCartao, LogOperacao
from app.templates import templates

router = APIRouter(prefix="/lancamentos", tags=["lancamentos"])


def _ctx_base(db: Session) -> dict:
    """Contexto comum para formulários de lançamento."""
    return {
        "contas_todas": db.query(ContaBancaria).order_by(ContaBancaria.tipo_conta, ContaBancaria.nome_conta).all(),
        "categorias": db.query(Categoria).order_by(
            func.coalesce(Categoria.categorias_pai_id, Categoria.categorias_id),
            Categoria.categorias_pai_id.isnot(None),
            Categoria.categorias_nome
        ).all(),
        "hoje": date.today().isoformat(),
    }


def get_or_create_fatura(db: Session, cartao_id: int, data_original: date) -> int:
    """
    Localiza a fatura aberta para o cartão e data informados.
    Se não encontrar, cria uma nova seguindo o dia de fechamento do cartão.
    """
    cartao = db.query(ContaBancaria).filter(ContaBancaria.conta_id == cartao_id).first()
    if not cartao:
        return None  # Ou lança erro se preferir
        
    dia_fechamento = cartao.contas_cartao_fechamento or 1
    dia_vencimento = cartao.contas_prev_debito or 10

    # Determina o mês de referência da fatura
    # Se a data for depois do fechamento, pertence à fatura do próximo mês (ou subsequente)
    if data_original.day >= dia_fechamento:
        # Próximo mês
        mes_ref = data_original.month % 12 + 1
        ano_ref = data_original.year + (1 if data_original.month == 12 else 0)
    else:
        mes_ref = data_original.month
        ano_ref = data_original.year

    data_ref = date(ano_ref, mes_ref, 1)

    # Busca fatura existente
    fatura = db.query(FaturaCartao).filter(
        FaturaCartao.conta_id == cartao_id,
        FaturaCartao.mes_referencia == data_ref,
        FaturaCartao.fechado == 0
    ).first()

    if fatura:
        return fatura.fatura_id

    # Cria nova fatura (Emergencial)
    vencimento = date(ano_ref, mes_ref, dia_vencimento)
    fechamento = date(ano_ref, mes_ref, dia_fechamento)
    
    # Ajuste simples se data_ref for diferente (ex: fechamento no mês anterior ao vencimento)
    # Por padrão, assume que fechamento e vencimento são no mesmo mês de referência
    
    nova_fatura = FaturaCartao(
        conta_id=cartao_id,
        data_vencimento=vencimento,
        data_fechamento=fechamento,
        mes_referencia=data_ref,
        fechado=0,
        valor_total=0
    )
    db.add(nova_fatura)
    db.flush() # Para pegar o ID
    
    # Log da criação do sistema
    log = LogOperacao(
        log_acao="SYSTEM",
        log_entidade="FATURA",
        log_entidade_id=nova_fatura.fatura_id,
        log_detalhes=f"Fatura criada automaticamente para {data_ref.strftime('%m/%Y')} (Cartão ID {cartao_id})"
    )
    db.add(log)
    
    return nova_fatura.fatura_id


def recalcular_total_fatura(db: Session, fatura_id: int):
    """Soma todas as operações de uma fatura e atualiza o campo valor_total."""
    fatura = db.query(FaturaCartao).filter(FaturaCartao.fatura_id == fatura_id).first()
    if not fatura:
        return

    total = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_fatura == fatura_id,
        Operacao.operacoes_validacao == 1
    ).scalar() or Decimal("0.00")

    fatura.valor_total = total
    db.commit()


def log_evento(db: Session, acao: str, entidade: str, entidade_id: int, detalhes: str, usuario_id: int = None):
    log = LogOperacao(
        log_usuario_id=usuario_id,
        log_acao=acao,
        log_entidade=entidade,
        log_entidade_id=entidade_id,
        log_detalhes=detalhes
    )
    db.add(log)


@router.get("/nova-despesa", response_class=HTMLResponse)
async def form_despesa(request: Request, sessao=Depends(require_login), db=Depends(get_db)):
    return templates.TemplateResponse("lancamentos/nova_despesa.html",
        {"request": request, "sessao": sessao, **_ctx_base(db)})


@router.get("/nova-receita", response_class=HTMLResponse)
async def form_receita(request: Request, sessao=Depends(require_login), db=Depends(get_db)):
    return templates.TemplateResponse("lancamentos/nova_receita.html",
        {"request": request, "sessao": sessao, **_ctx_base(db)})


@router.get("/nova-transferencia", response_class=HTMLResponse)
async def form_transferencia(request: Request, sessao=Depends(require_login), db=Depends(get_db)):
    return templates.TemplateResponse("lancamentos/nova_transferencia.html",
        {"request": request, "sessao": sessao, **_ctx_base(db)})



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
    repetir: Optional[str] = Form(default=None),
    modo_repeticao: Optional[str] = Form(default="parcelado"),
    num_parcelas: Optional[int] = Form(default=2),
    frequencia: Optional[str] = Form(default="mensal"),
    ocorrencias: Optional[int] = Form(default=12),
    adicional_id: Optional[int] = Form(default=None),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
) :

    conta_origem_obj = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta).first()
    conta_destino_obj = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta_destino).first()

    nome_origem = conta_origem_obj.nome_conta if conta_origem_obj else f"ID {conta}"
    nome_destino = conta_destino_obj.nome_conta if conta_destino_obj else f"ID {conta_destino}"

    # Limpa formatação BRL (1.800,00 -> 1800.00)
    valor_limpo = valor.replace(".", "").replace(",", ".")
    valor_float = float(valor_limpo)
    valor_final = valor_float

    # Converte fatura para int ou None (lidando com strings vazias do form)
    fat_id = int(fatura) if fatura and fatura.strip() else None

    # NOTA: não sobrescrevemos 'conta' com fat_id — conta é o conta_id real do cartão
    parcela_str = None
    if parcela_atual and parcela_total:
        parcela_str = f"{parcela_atual:03d}.{parcela_total:03d}"

    dt_operacao = date.fromisoformat(data)

    # Lógica de Fatura Inteligente: usa fat_id como base para localizar a fatura correta
    final_fat_id = fat_id

    dt_efetivado = None
    if efetivado:
        if data_efetivado:
            dt_efetivado = datetime.fromisoformat(data_efetivado)
        else:
            dt_efetivado = datetime.now()

    grupo_id = f"TRF-{uuid.uuid4().hex[:10]}"
    
    # Se o parâmetro repetir vier como string "on" (checkbox), converte para bool
    is_repetir = repetir == "on" or repetir == "1" or repetir is True
    
    repeticoes = 1
    if is_repetir:
        if modo_repeticao == "parcelado":
            repeticoes = num_parcelas or 2
        else:
            repeticoes = ocorrencias or 12

    # Lógica de Cálculo: Total vs Parcela
    valor_unitario = valor_final
    resto_divisao = 0
    if is_repetir and valor_total_ou_parcela == "total":
        valor_unitario = round(valor_final / repeticoes, 2)
        resto_divisao = valor_final - (valor_unitario * repeticoes)

    faturas_para_recalcular = set()
    for i in range(repeticoes):
        curr_dt = dt_operacao
        if i > 0:
            if modo_repeticao == "parcelado" or frequencia == "mensal":
                curr_dt = dt_operacao + relativedelta(months=i)
            elif frequencia == "semanal":
                curr_dt = dt_operacao + timedelta(weeks=i)
            elif frequencia == "anual":
                curr_dt = dt_operacao + relativedelta(years=i)

        # Valor ajustado para a última parcela
        curr_valor_f = valor_unitario
        if i == repeticoes - 1:
            curr_valor_f += resto_divisao
        
        curr_parcela = None
        if modo_repeticao == "parcelado":
            curr_parcela = f"{i+1:03d}.{num_parcelas:03d}"
        elif parcela_str:
            curr_parcela = parcela_str

        # Define data_efetivado apenas para o primeiro da série se for o caso
        curr_efetivado = efetivado if i == 0 else 0
        curr_dt_efetivado = dt_efetivado if i == 0 else None

        curr_fat_saida = get_or_create_fatura(db, conta, curr_dt) if fat_id else None
        curr_fat_entrada = get_or_create_fatura(db, conta_destino, curr_dt) if fat_id else None

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
            operacoes_adicional_id=adicional_id
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
            operacoes_adicional_id=adicional_id
        )
        db.add(op_saida); db.add(op_entrada); db.flush()

        if curr_fat_saida: faturas_para_recalcular.add(curr_fat_saida)
        if curr_fat_entrada: faturas_para_recalcular.add(curr_fat_entrada)
        op_saida.operacoes_transf_rel = op_entrada.operacoes_id
        op_entrada.operacoes_transf_rel = op_saida.operacoes_id

        if i == 0:
            log_evento(db, "INSERT", "TRANSFERÊNCIA", op_saida.operacoes_id, f"Série '{descricao}' compartilhada no Grupo {grupo_id}", sessao.get("id"))
    
    db.commit()
    
    for f_id in faturas_para_recalcular:
        recalcular_total_fatura(db, f_id)
    
    redirecionar = next_url or request.headers.get("referer") or f"/extrato?c={conta}"
    return RedirectResponse(url=redirecionar, status_code=303)




@router.post("/inserir")
async def inserir_lancamento(
    request: Request,
    descricao: str = Form(...), #
    conta: Optional[int] = Form(default=None), #
    valor: str = Form(...), #
    tipo: int = Form(...), #
    data: str = Form(...), #
    categoria: Optional[str] = Form(default=None), #
    fatura: Optional[str] = Form(default=None),
    parcela_atual: Optional[int] = Form(default=None),
    parcela_total: Optional[int] = Form(default=None),
    next_url: Optional[str] = Form(default=None),
    efetivado: int = Form(default=0),
    data_efetivado: Optional[str] = Form(default=None),
    repetir: Optional[str] = Form(default=None), #
    modo_repeticao: Optional[str] = Form(default="parcelado"), #
    num_parcelas: Optional[int] = Form(default=2), #
    valor_total_ou_parcela: Optional[str] = Form(default="total"), #
    frequencia: Optional[str] = Form(default="mensal"), #
    ocorrencias: Optional[int] = Form(default=12), #
    adicional_id: Optional[str] = Form(default=None),
    conta_destino: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    # Limpa formatação BRL (1.800,00 -> 1800.00)
    valor_limpo = valor.replace(".", "").replace(",", ".")
    valor_float = float(valor_limpo)
    valor_final = -abs(valor_float) if tipo == 3 else abs(valor_float)

    # Converte categoria e fatura para int ou None (lidando com strings vazias do form)
    cat_id = int(categoria) if categoria and categoria.strip() and categoria != "-1" else None
    fat_id = int(fatura) if fatura and fatura.strip() else None

    # Lógica Inteligente: Se a conta selecionada for um Cartão (tipo 4),
    # PRECISAMOS de uma fatura, mesmo que o usuário não tenha marcado o toggle.
    acct_obj = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta).first()
    if acct_obj and acct_obj.tipo_conta == 4:
        fat_id = conta # O fat_id no get_or_create_fatura usa o ID da conta do cartão

    # Regra de negócio: Se for lançamento em fatura, o 'tipo' deve ser despesa (3) ou receita (1)
    # mas o 'conta' (ID da conta_bancaria) deve ser o do cartão.
    if fat_id:
        conta = fat_id

    parcela_str = None
    if parcela_atual and parcela_total:
        parcela_str = f"{parcela_atual:03d}.{parcela_total:03d}"

    # Conversão de destinos e adicionais (podem vir como strings vazias)
    conta_destino_id = int(conta_destino) if conta_destino and conta_destino.strip() and conta_destino != "-1" else None
    add_id = int(adicional_id) if adicional_id and adicional_id.strip() and adicional_id != "-1" else None

    dt_operacao = date.fromisoformat(data)
    
    # Lógica de Fatura Inteligente
    final_fat_id = fat_id
    if fat_id:
        final_fat_id = get_or_create_fatura(db, conta, dt_operacao)

    dt_efetivado = None
    if efetivado:
        if data_efetivado:
            dt_efetivado = datetime.fromisoformat(data_efetivado)
        else:
            dt_efetivado = datetime.now()

    grupo_id = f"GAP-{uuid.uuid4().hex[:10]}" if (parcela_str or repetir) else None
    
    is_repetir = repetir == "on" or repetir == "1" or repetir is True
    repeticoes = 1
    if is_repetir:
        if modo_repeticao == "parcelado":
            repeticoes = num_parcelas or 2
        else:
            repeticoes = ocorrencias or 12

    # Lógica de Cálculo: Total vs Parcela
    valor_unitario = valor_final
    resto_divisao = 0
    if is_repetir and valor_total_ou_parcela == "total":
        valor_unitario = round(valor_final / repeticoes, 2)
        # Calcula a diferença de arredondamento para a última parcela
        resto_divisao = valor_final - (valor_unitario * repeticoes)

    faturas_para_recalcular = set()
    for i in range(repeticoes):
        curr_dt = dt_operacao
        if i > 0:
            if modo_repeticao == "parcelado" or frequencia == "mensal":
                curr_dt = dt_operacao + relativedelta(months=i)
            elif frequencia == "semanal":
                curr_dt = dt_operacao + timedelta(weeks=i)
            elif frequencia == "anual":
                curr_dt = dt_operacao + relativedelta(years=i)
        
        # Valor ajustado para a última parcela
        curr_valor = valor_unitario
        if i == repeticoes - 1:
            curr_valor += resto_divisao

        curr_parcela = None
        if modo_repeticao == "parcelado":
            curr_parcela = f"{i+1:03d}.{repeticoes:03d}"
        elif parcela_str:
            curr_parcela = parcela_str

        curr_efetivado = efetivado if i == 0 else 0
        curr_dt_efetivado = dt_efetivado if i == 0 else None
        
        curr_fat_id = get_or_create_fatura(db, conta, curr_dt) if fat_id else None

        op = Operacao(
            operacoes_data_lancamento=curr_dt,
            operacoes_descricao=descricao,
            operacoes_conta=conta,
            operacoes_valor=curr_valor,
            operacoes_tipo=tipo,
            operacoes_categoria=cat_id,
            operacoes_fatura=curr_fat_id,
            operacoes_parcela=curr_parcela,
            operacoes_efetivado=curr_efetivado,
            operacoes_data_efetivado=curr_dt_efetivado,
            operacoes_validacao=1,
            operacoes_grupo_id=grupo_id,
            operacoes_adicional_id=add_id
        )
        db.add(op)
        
        # Lógica de Transferência: Criar a contraparte se for tipo 4
        if tipo == 4 and conta_destino_id:
            op_destino = Operacao(
                operacoes_data_lancamento=curr_dt,
                operacoes_descricao=descricao,
                operacoes_conta=conta_destino_id,
                operacoes_valor=abs(curr_valor), # Entrada sempre positiva
                operacoes_tipo=tipo,
                operacoes_categoria=None,
                operacoes_fatura=None, # Transferência entra na conta, não na fatura (geralmente)
                operacoes_parcela=curr_parcela,
                operacoes_efetivado=curr_efetivado,
                operacoes_data_efetivado=curr_dt_efetivado,
                operacoes_validacao=1,
                operacoes_grupo_id=grupo_id,
                operacoes_transf_rel=op.operacoes_id # Primeiro ID provisório, corrigiremos abaixo
            )
            db.add(op_destino)
            db.flush()
            op.operacoes_transf_rel = op_destino.operacoes_id
            op_destino.operacoes_transf_rel = op.operacoes_id
        
        if curr_fat_id:
            faturas_para_recalcular.add(curr_fat_id)
        
        db.flush()
        if i == 0:
            log_evento(db, "INSERT", f"LANÇAMENTO {tipo}", op.operacoes_id, f"Início de série '{descricao}' no Grupo {grupo_id}", sessao.get("id"))

    db.commit()

    for f_id in faturas_para_recalcular:
        recalcular_total_fatura(db, f_id)
    
    redirecionar = next_url or request.headers.get("referer") or f"/extrato?c={conta}"
    return RedirectResponse(url=redirecionar, status_code=303)


@router.post("/efetivar/{op_id}")
async def efetivar(request: Request, op_id: int, db=Depends(get_db), sessao=Depends(require_login)):
    from datetime import datetime
    op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if op:
        # agora = datetime.now()
        op.operacoes_efetivado = 1
        op.operacoes_data_efetivado = op.operacoes_data_lancamento
        
        # Se for transferência, efetiva o outro lado também
        if op.operacoes_transf_rel:
            rel = db.query(Operacao).filter(Operacao.operacoes_id == op.operacoes_transf_rel).first()
            if rel:
                rel.operacoes_efetivado = 1
                rel.operacoes_data_efetivado = op.operacoes_data_lancamento
                log_evento(db, "UPDATE", "OPERACAO", rel.operacoes_id, f"Efetivado lado relacionado da transferência '{rel.operacoes_descricao}'", sessao.get("id"))
        
        log_evento(db, "UPDATE", "OPERACAO", op.operacoes_id, f"Efetivado lançamento '{op.operacoes_descricao}'", sessao.get("id"))
        
        db.commit()
        if op.operacoes_fatura: recalcular_total_fatura(db, op.operacoes_fatura)
    return RedirectResponse(url=request.headers.get("referer", "/dashboard"), status_code=303)


@router.post("/deletar/{op_id}")
async def deletar(request: Request, op_id: int, db=Depends(get_db), sessao=Depends(require_login)):
    op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if op:
        fat_id = op.operacoes_fatura
        rel_fat_id = None
        desc_backup = op.operacoes_descricao
        if op.operacoes_transf_rel:
            rel = db.query(Operacao).filter(Operacao.operacoes_id == op.operacoes_transf_rel).first()
            if rel:
                # Log da exclusão relacionada (transferência)
                log_evento(db, "DELETE", "OPERACAO", rel.operacoes_id, f"Removido lançamento relacionado (Transferência) '{rel.operacoes_descricao}'", sessao.get("id"))
                db.delete(rel)
        
        # Log da exclusão
        log_evento(db, "DELETE", "OPERACAO", op_id, f"Removido lançamento '{desc_backup}'", sessao.get("id"))
        
        db.delete(op); db.commit()

        # Recalcula
        if fat_id: recalcular_total_fatura(db, fat_id)
        if rel_fat_id: recalcular_total_fatura(db, rel_fat_id)
    return RedirectResponse(url=request.headers.get("referer", "/dashboard"), status_code=303)


@router.get("/editar/{op_id}")
async def editar_get(
    op_id: int,
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    """Retorna JSON com os dados do lançamento para preencher o modal dinamicamente."""
    op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if not op:
        return JSONResponse(status_code=404, content={"erro": "Lançamento não encontrado"})

    # Descobre a conta origem e destino corretamente para transferências
    conta_origem_id = op.operacoes_conta
    conta_destino_id = None
    
    if op.operacoes_transf_rel:
        op_rel = db.query(Operacao).filter(Operacao.operacoes_id == op.operacoes_transf_rel).first()
        if op_rel:
            # Se clicamos na ENTRADA (valor > 0), a 'conta' do formulário (origem) deve ser a conta da outra ponta.
            # E a 'conta_destino' deve ser a conta desta ponta (entrada).
            if float(op.operacoes_valor) > 0:
                conta_origem_id = op_rel.operacoes_conta
                conta_destino_id = op.operacoes_conta
            else:
                # Se clicamos na SAÍDA (valor < 0), a conta de origem é esta, e a de destino é a outra.
                conta_origem_id = op.operacoes_conta
                conta_destino_id = op_rel.operacoes_conta

    valor_safe = float(op.operacoes_valor) if op.operacoes_valor is not None else 0.0
    conta_origem_safe = int(conta_origem_id) if conta_origem_id is not None else None
    conta_destino_safe = int(conta_destino_id) if conta_destino_id is not None else None

    return JSONResponse(content=jsonable_encoder({
        "id": int(op.operacoes_id),
        "descricao": op.operacoes_descricao or "",
        "valor": float(abs(valor_safe)),
        "tipo": int(op.operacoes_tipo) if op.operacoes_tipo is not None else None,
        "data": op.operacoes_data_lancamento.isoformat() if op.operacoes_data_lancamento else None,
        "categoria": int(op.operacoes_categoria) if op.operacoes_categoria is not None else None,
        "conta": conta_origem_safe,
        "conta_destino": conta_destino_safe,
        "transf_rel": int(op.operacoes_transf_rel) if op.operacoes_transf_rel is not None else None,
        "parcela": op.operacoes_parcela,
        "recorrencia": int(op.operacoes_recorrencia) if op.operacoes_recorrencia is not None else None,
        "efetivado": int(op.operacoes_efetivado) if op.operacoes_efetivado is not None else 0,
        "data_efetivado": op.operacoes_data_efetivado.strftime("%Y-%m-%d") if op.operacoes_data_efetivado else None,
        "adicional_id": op.operacoes_adicional_id,
    }))



@router.post("/editar/{op_id}")
async def editar(
    request: Request,
    op_id: int,
    descricao: str = Form(...),
    conta: int = Form(...),
    valor: str = Form(...),
    tipo: int = Form(...),
    data: str = Form(...),
    categoria: Optional[str] = Form(default=None),
    escopo: str = Form(default="so_este"),
    efetivado: int = Form(default=0),
    data_efetivado: Optional[str] = Form(default=None),
    adicional_id: Optional[int] = Form(default=None),
    nova_parcela_total: Optional[int] = Form(default=None),
    next_url: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if not op:
        redirecionar = next_url or request.headers.get("referer") or "/dashboard"
        return RedirectResponse(url=redirecionar, status_code=303)

        
    # Limpa formatação BRL
    valor_limpo = valor.replace(".", "").replace(",", ".")
    valor_float = float(valor_limpo)
    valor_final = -abs(valor_float) if tipo == 3 else abs(valor_float)
    dt_operacao = date.fromisoformat(data)
    cat_id = int(categoria) if categoria and str(categoria).strip() else None

    detalhes = f"Editado: '{op.operacoes_descricao}' -> '{descricao}'. R$ {op.operacoes_valor} -> R$ {valor_final}"

    def _aplicar_edicao(operacao: Operacao):
        """Aplica os campos editados em uma operação."""
        sinal = -1 if float(operacao.operacoes_valor) < 0 else 1
        operacao.operacoes_descricao = descricao
        operacao.operacoes_conta = conta
        operacao.operacoes_valor = abs(valor_float) * sinal if tipo == 3 else abs(valor_float) * sinal
        operacao.operacoes_valor = valor_final
        operacao.operacoes_tipo = tipo
        operacao.operacoes_data_lancamento = dt_operacao
        operacao.operacoes_categoria = cat_id
        operacao.operacoes_adicional_id = adicional_id

    log_evento(db, "UPDATE", "OPERACAO", op_id, detalhes, sessao.get("id"))
    
    # IMPORTANTE: Captura faturas e grupo_id ANTES das alterações
    old_fat_id = op.operacoes_fatura
    old_rel_fat_id = None
    if op.operacoes_transf_rel:
        rel_op = db.query(Operacao).filter(Operacao.operacoes_id == op.operacoes_transf_rel).first()
        if rel_op: old_rel_fat_id = rel_op.operacoes_fatura

    old_date = op.operacoes_data_lancamento
    grupo_id = op.operacoes_grupo_id
    
    # Captura total de parcelas antigo para lógica de adição/remoção
    old_parcela_total = None
    if op.operacoes_parcela:
        p_sep = "/" if "/" in str(op.operacoes_parcela) else "."
        p_partes = str(op.operacoes_parcela).split(p_sep)
        if len(p_partes) == 2:
            try:
                old_parcela_total = int(p_partes[1])
            except: pass

    _aplicar_edicao(op)
    op.operacoes_efetivado = efetivado
    if efetivado and data_efetivado:
        op.operacoes_data_efetivado = datetime.fromisoformat(data_efetivado)
    elif efetivado and not op.operacoes_data_efetivado:
        op.operacoes_data_efetivado = datetime.now()
    elif not efetivado:
        op.operacoes_data_efetivado = None

    # Atualiza parcela se solicitado
    if nova_parcela_total and op.operacoes_parcela:
        p_sep = "/" if "/" in str(op.operacoes_parcela) else "."
        atual = int(str(op.operacoes_parcela).split(p_sep)[0])
        op.operacoes_parcela = f"{atual:03d}{p_sep}{nova_parcela_total:03d}"

    # Edição em cascata robusta
    if escopo == "subsequentes":
        new_date = dt_operacao
        ops_seguintes = []

        if grupo_id:
            ops_seguintes = db.query(Operacao).filter(
                Operacao.operacoes_grupo_id == grupo_id,
                Operacao.operacoes_data_lancamento > old_date,
                Operacao.operacoes_validacao == 1,
            ).order_by(Operacao.operacoes_data_lancamento).all()
        elif op.operacoes_parcela:
            # Fallback para parcelas sem grupo_id
            p_sep = "/" if "/" in str(op.operacoes_parcela) else "."
            parcela_total_num = int(str(op.operacoes_parcela).split(p_sep)[1])
            ops_seguintes = db.query(Operacao).filter(
                Operacao.operacoes_descricao == op.operacoes_descricao,
                Operacao.operacoes_conta == op.operacoes_conta,
                Operacao.operacoes_data_lancamento > old_date,
                Operacao.operacoes_validacao == 1,
            ).all()
            ops_seguintes = [o for o in ops_seguintes if str(o.operacoes_parcela).endswith(f"{p_sep}{parcela_total_num:03d}")]

        for o in ops_seguintes:
            # Sincroniza campos básicos
            o.operacoes_descricao = descricao
            o.operacoes_categoria = cat_id
            o.operacoes_valor = valor_final
            o.operacoes_conta = conta
            o.operacoes_tipo = tipo
            
            # Atualiza total de parcelas se necessário
            if nova_parcela_total and o.operacoes_parcela:
                p_sep = "/" if "/" in str(o.operacoes_parcela) else "."
                o_atual = int(str(o.operacoes_parcela).split(p_sep)[0])
                o.operacoes_parcela = f"{o_atual:03d}{p_sep}{nova_parcela_total:03d}"

            # Ajuste de data inteligente
            if new_date != old_date:
                diff = relativedelta(o.operacoes_data_lancamento, old_date)
                o.operacoes_data_lancamento = new_date + diff
            
            # Se não tinha grupo_id, aproveitamos para atribuir o do pai (ou criar um se o pai não tinha)
            if not o.operacoes_grupo_id:
                if not op.operacoes_grupo_id:
                     op.operacoes_grupo_id = f"GAP-{uuid.uuid4().hex[:10]}"
                o.operacoes_grupo_id = op.operacoes_grupo_id
            
            log_evento(db, "UPDATE", "OPERACAO", o.operacoes_id,
                       f"Editado em cascata: '{descricao}'", sessao.get("id"))

        # --- LÓGICA DE ADICIONAR OU REMOVER PARCELAS FISICAMENTE ---
        if nova_parcela_total and old_parcela_total and nova_parcela_total != old_parcela_total:
            p_sep = "/" if "/" in str(op.operacoes_parcela) else "."
            
            if nova_parcela_total < old_parcela_total:
                # 1. Remover excedentes (se o total diminuiu)
                query_del = db.query(Operacao).filter(Operacao.operacoes_validacao == 1)
                if grupo_id:
                    query_del = query_del.filter(Operacao.operacoes_grupo_id == grupo_id)
                else:
                    query_del = query_del.filter(Operacao.operacoes_descricao == op.operacoes_descricao, Operacao.operacoes_conta == op.operacoes_conta)
                
                rows = query_del.all()
                for r in rows:
                    try:
                        r_idx = int(str(r.operacoes_parcela).split(p_sep)[0])
                        # Deleta se o index for maior que o novo total e se não for o objeto que estamos editando
                        if r_idx > nova_parcela_total and r.operacoes_id != op.operacoes_id:
                            log_evento(db, "DELETE", "OPERACAO", r.operacoes_id, f"Removido por redução de parcelas ({old_parcela_total} -> {nova_parcela_total})", sessao.get("id"))
                            f_id = r.operacoes_fatura
                            db.delete(r)
                            db.flush()
                            if f_id: recalcular_total_fatura(db, f_id)
                    except: continue

            elif nova_parcela_total > old_parcela_total:
                # 2. Adicionar novas (se o total aumentou)
                query_series = db.query(Operacao).filter(Operacao.operacoes_validacao == 1)
                if grupo_id:
                    query_series = query_series.filter(Operacao.operacoes_grupo_id == grupo_id)
                else:
                    query_series = query_series.filter(Operacao.operacoes_descricao == op.operacoes_descricao, Operacao.operacoes_conta == op.operacoes_conta)
                
                all_members = query_series.all()
                if not grupo_id:
                    all_members = [m for m in all_members if p_sep in str(m.operacoes_parcela) and str(m.operacoes_parcela).endswith(f"{p_sep}{old_parcela_total:03d}")]
                
                if all_members:
                    all_members.sort(key=lambda x: int(str(x.operacoes_parcela).split(p_sep)[0]))
                    last_member = all_members[-1]
                    last_idx = int(str(last_member.operacoes_parcela).split(p_sep)[0])
                    last_dt = last_member.operacoes_data_lancamento
                    
                    for i in range(last_idx + 1, nova_parcela_total + 1):
                        proxima_data = last_dt + relativedelta(months=(i - last_idx))
                        
                        nova_op = Operacao(
                            operacoes_descricao=descricao,
                            operacoes_conta=conta,
                            operacoes_valor=valor_final,
                            operacoes_tipo=tipo,
                            operacoes_data_lancamento=proxima_data,
                            operacoes_categoria=cat_id,
                            operacoes_parcela=f"{i:03d}{p_sep}{nova_parcela_total:03d}",
                            operacoes_validacao=1,
                            operacoes_efetivado=0,
                            operacoes_grupo_id=grupo_id,
                            operacoes_adicional_id=adicional_id
                        )
                        
                        conta_obj = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta).first()
                        if conta_obj and conta_obj.tipo_conta == 4:
                            nova_op.operacoes_fatura = get_or_create_fatura(db, conta, proxima_data)
                        
                        db.add(nova_op)
                        db.flush()
                        log_evento(db, "INSERT", "OPERACAO", nova_op.operacoes_id, f"Adicionado por expansão de parcelas ({old_parcela_total} -> {nova_parcela_total})", sessao.get("id"))
                        if nova_op.operacoes_fatura:
                            recalcular_total_fatura(db, nova_op.operacoes_fatura)

    db.commit()

    redirecionar = next_url or request.headers.get("referer") or "/dashboard"
    return RedirectResponse(url=redirecionar, status_code=303)


@router.post("/editar-transferencia/{op_id}")
async def editar_transferencia(
    request: Request,
    op_id: int,
    descricao: str = Form(...),
    conta: int = Form(...),
    conta_destino: int = Form(...),
    valor: str = Form(...),
    data: str = Form(...),
    adicional_id: Optional[int] = Form(default=None),
    escopo: str = Form(default="so_este"),
    next_url: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    """Edita o par de operações de uma transferência (saída + entrada espelho)."""
    # Busca a operação principal (pode ser saída ou entrada)
    op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if not op:
        redirecionar = next_url or request.headers.get("referer") or "/dashboard"
        return RedirectResponse(url=redirecionar, status_code=303)

    # Garante que temos a operação de SAÍDA (valor negativo)
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

    # IMPORTANTE: Captura a data original e grupo_id para a lógica de cascata ANTES de modificar as operações
    old_date = op.operacoes_data_lancamento
    grupo_id = op.operacoes_grupo_id

    detalhes = f"Transferência editada: '{descricao}' R$ {valor_float} de conta {conta} -> {conta_destino}"

    if op_saida:
        op_saida.operacoes_descricao = descricao
        op_saida.operacoes_conta = conta
        op_saida.operacoes_valor = -abs(valor_float)
        op_saida.operacoes_data_lancamento = dt_operacao
        op_saida.operacoes_adicional_id = adicional_id
        log_evento(db, "UPDATE", "OPERACAO", op_saida.operacoes_id, detalhes, sessao.get("id"))

    if op_entrada:
        op_entrada.operacoes_descricao = descricao
        op_entrada.operacoes_conta = conta_destino
        op_entrada.operacoes_valor = abs(valor_float)
        op_entrada.operacoes_data_lancamento = dt_operacao
        # A conta de entrada (destino) geralmente não tem o adicional_id vinculado se for conta corrente
        # mas mantemos a coerência se for outra ponta de cartão (incomum)
        log_evento(db, "UPDATE", "OPERACAO", op_entrada.operacoes_id, detalhes, sessao.get("id"))

    # Sincronização em cascata para transferências
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
            # Fallback (menos preciso)
            ops_seguintes = db.query(Operacao).filter(
                Operacao.operacoes_descricao == op.operacoes_descricao,
                Operacao.operacoes_data_lancamento > old_date,
                Operacao.operacoes_validacao == 1
            ).all()

        for s_op in ops_seguintes:
            s_op.operacoes_descricao = descricao
            
            # Ajuste de data inteligente
            if new_date != old_date:
                diff = relativedelta(s_op.operacoes_data_lancamento, old_date)
                s_op.operacoes_data_lancamento = new_date + diff
            
            # Sincroniza valor e conta dependendo se é entrada ou saída
            if float(s_op.operacoes_valor) < 0:
                s_op.operacoes_valor = -abs(valor_float)
                s_op.operacoes_conta = conta_origem
            else:
                s_op.operacoes_valor = abs(valor_float)
                s_op.operacoes_conta = conta_destino
            
            if not s_op.operacoes_grupo_id:
                 if not op.operacoes_grupo_id:
                      op.operacoes_grupo_id = f"GAP-{uuid.uuid4().hex[:10]}"
                 s_op.operacoes_grupo_id = op.operacoes_grupo_id

            log_evento(db, "UPDATE", "OPERACAO", s_op.operacoes_id,
                       f"Transferência editada em cascata: '{descricao}'", sessao.get("id"))

    db.commit()

    redirecionar = next_url or request.headers.get("referer") or "/dashboard"
    return RedirectResponse(url=redirecionar, status_code=303)


@router.post("/duplicar/{op_id}")
async def duplicar(request: Request, op_id: int, db=Depends(get_db), sessao=Depends(require_login)):
    """Clona uma operação, mantendo-a como pendente."""
    op_origem = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if op_origem:
        # Criamos uma nova instância ignorando campos de auto-incremento e efetivação
        nova_op = Operacao(
            operacoes_data_lancamento=op_origem.operacoes_data_lancamento,
            operacoes_descricao=f"{op_origem.operacoes_descricao} (Cópia)",
            operacoes_conta=op_origem.operacoes_conta,
            operacoes_valor=op_origem.operacoes_valor,
            operacoes_tipo=op_origem.operacoes_tipo,
            operacoes_categoria=op_origem.operacoes_categoria,
            operacoes_fatura=op_origem.operacoes_fatura,
            operacoes_parcela=op_origem.operacoes_parcela,
            operacoes_transf_rel=op_origem.operacoes_transf_rel,
            operacoes_recorrencia=op_origem.operacoes_recorrencia,
            operacoes_projeto=op_origem.operacoes_projeto,
            operacoes_efetivado=0, # Sempre nasce como pendente
            operacoes_validacao=1,
        )
        db.add(nova_op)
        db.flush()
        
        # Log da duplicação
        log_evento(db, "INSERT", "OPERACAO", nova_op.operacoes_id, f"Duplicado lançamento '{op_origem.operacoes_descricao}'", sessao.get("id"))
        
        db.commit()
        if nova_op.operacoes_fatura: recalcular_total_fatura(db, nova_op.operacoes_fatura)
    
    return RedirectResponse(url=request.headers.get("referer", "/dashboard"), status_code=303)


@router.post("/converter-para-transferencia/{op_id}")
async def converter_para_transferencia(
    op_id: int,
    conta_destino_id: int = Form(...),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if not op:
        return JSONResponse(status_code=404, content={"erro": "Lançamento não encontrado"})

    if op.operacoes_transf_rel:
        return JSONResponse(status_code=400, content={"erro": "Este lançamento já é uma transferência"})

    # Valor original (sempre positivo no banco para R/D)
    valor_original = float(op.operacoes_valor)
    
    # Determina se o original era Receita (1) ou Despesa (3)
    # Se era Receita, a 'conta' atual recebeu dinheiro. Então na transferência ela é o DESTINO.
    # Se era Despesa, a 'conta' atual pagou dinheiro. Então na transferência ela é a ORIGEM.
    
    nova_descricao = f"Transferência: {op.operacoes_descricao}"
    grupo_id = op.operacoes_grupo_id or f"TRF-CONV-{uuid.uuid4().hex[:8]}"

    # Criamos a contraparte
    op_espelho = Operacao(
        operacoes_data_lancamento=op.operacoes_data_lancamento,
        operacoes_descricao=nova_descricao,
        operacoes_conta=conta_destino_id,
        operacoes_valor=valor_original if int(op.operacoes_tipo) == 3 else -valor_original,
        operacoes_tipo="4",
        operacoes_efetivado=op.operacoes_efetivado,
        operacoes_data_efetivado=op.operacoes_data_efetivado,
        operacoes_validacao=1,
        operacoes_grupo_id=grupo_id
    )
    
    # Atualizamos o original
    # Pera, o tipo no banco para transferência é sempre "4". 
    # O valor é que define se é entrada ou saída.
    if int(op.operacoes_tipo) == 1: # Era receita, agora é entrada da transferência
        op.operacoes_valor = valor_original
        op_espelho.operacoes_valor = -valor_original
    else: # Era despesa, agora é saída da transferência
        op.operacoes_valor = -valor_original
        op_espelho.operacoes_valor = valor_original

    op.operacoes_tipo = "4"
    op.operacoes_categoria = None # Transferência não tem categoria
    op.operacoes_grupo_id = grupo_id

    db.add(op_espelho)
    db.flush() # Para pegar o ID do espelho

    op.operacoes_transf_rel = op_espelho.operacoes_id
    op_espelho.operacoes_transf_rel = op.operacoes_id

    log_evento(db, "UPDATE", "OPERACAO", op.operacoes_id, f"Convertido para transferência (Destino: {conta_destino_id})", sessao.get("id"))
    db.commit()

    # Recalcula se necessário
    if op.operacoes_fatura: recalcular_total_fatura(db, op.operacoes_fatura)
    if op_espelho.operacoes_fatura: recalcular_total_fatura(db, op_espelho.operacoes_fatura)

    return JSONResponse(content={"sucesso": True, "id_original": op.operacoes_id, "id_espelho": op_espelho.operacoes_id})


@router.post("/conciliar-massa")
async def conciliar_massa(
    ids: str = Form(...),
    back_url: Optional[str] = Form(default="/extrato"),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    """Concilia múltiplos lançamentos de uma vez."""
    op_ids = json.loads(ids)
    faturas_recalc = set()
    
    for op_id in op_ids:
        op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
        if op:
            op.operacoes_efetivado = 1
            op.operacoes_data_efetivado = datetime.now()
            if op.operacoes_fatura: faturas_recalc.add(op.operacoes_fatura)
            
            # Se for transferência, concilia o outro lado
            if op.operacoes_transf_rel:
                op_rel = db.query(Operacao).filter(Operacao.operacoes_id == op.operacoes_transf_rel).first()
                if op_rel:
                    op_rel.operacoes_efetivado = 1
                    op_rel.operacoes_data_efetivado = datetime.now()
                    if op_rel.operacoes_fatura: faturas_recalc.add(op_rel.operacoes_fatura)

    db.commit()
    for f_id in faturas_recalc: recalcular_total_fatura(db, f_id)
    return RedirectResponse(url=back_url, status_code=303)


@router.post("/deletar-massa")
async def deletar_massa(
    ids: str = Form(...),
    back_url: Optional[str] = Form(default="/extrato"),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    """Exclui múltiplos lançamentos de uma vez."""
    op_ids = json.loads(ids)
    faturas_recalc = set()
    
    for op_id in op_ids:
        op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
        if op:
            if op.operacoes_fatura: faturas_recalc.add(op.operacoes_fatura)
            
            if op.operacoes_transf_rel:
                op_rel = db.query(Operacao).filter(Operacao.operacoes_id == op.operacoes_transf_rel).first()
                if op_rel:
                    if op_rel.operacoes_fatura: faturas_recalc.add(op_rel.operacoes_fatura)
                    db.delete(op_rel)
            
            db.delete(op)

    db.commit()
    for f_id in faturas_recalc: recalcular_total_fatura(db, f_id)
    return RedirectResponse(url=back_url, status_code=303)


@router.post("/editar-massa")
async def editar_massa(
    ids: str = Form(...),
    categoria_id: Optional[int] = Form(default=None),
    adicional_id: Optional[int] = Form(default=None),
    back_url: Optional[str] = Form(default="/extrato"),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    """Edita categoria ou portador de múltiplos lançamentos."""
    op_ids = json.loads(ids)
    
    for op_id in op_ids:
        op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
        if op:
            if categoria_id is not None:
                # Só altera categoria se não for transferência (tipo 4)
                if int(op.operacoes_tipo) != 4:
                    op.operacoes_categoria = categoria_id if categoria_id != -1 else None
            
            if adicional_id is not None:
                op.operacoes_adicional_id = adicional_id if adicional_id != -1 else None

    db.commit()
    return RedirectResponse(url=back_url, status_code=303)
