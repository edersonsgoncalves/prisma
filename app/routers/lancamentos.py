from datetime import date, datetime, timedelta
from typing import Optional
from calendar import monthrange
import uuid
from dateutil.relativedelta import relativedelta

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
        "contas": db.query(ContaBancaria).filter(ContaBancaria.tipo_conta != 4).all(),
        "cartoes": db.query(ContaBancaria).filter(ContaBancaria.tipo_conta == 4).all(),
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
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):

    conta_origem_obj = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta).first()
    conta_destino_obj = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta_destino).first()

    nome_origem = conta_origem_obj.nome_conta if conta_origem_obj else f"ID {conta}"
    nome_destino = conta_destino_obj.nome_conta if conta_destino_obj else f"ID {conta_destino}"

    # Limpa formatação BRL (1.800,00 -> 1800.00)
    valor_limpo = valor.replace(".", "").replace(",", ".")
    valor_float = float(valor_limpo)
    valor_final = valor_float

    # Converte categoria e fatura para int ou None (lidando com strings vazias do form)
    fat_id = int(fatura) if fatura and fatura.strip() else None

    # Se for cartão, a conta da operação deve ser o próprio cartão (fat_id)
    if fat_id:
        conta = fat_id

    parcela_str = None
    if parcela_atual and parcela_total:
        parcela_str = f"{parcela_atual:03d}.{parcela_total:03d}"

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

    grupo_id = f"TRF-{uuid.uuid4().hex[:10]}"
    
    # Se o parâmetro repetir vier como string "on" (checkbox), converte para bool
    is_repetir = repetir == "on" or repetir == "1" or repetir is True
    
    repeticoes = 1
    if repetir:
        if modo_repeticao == "parcelado":
            repeticoes = num_parcelas
        else:
            repeticoes = ocorrencias

    for i in range(repeticoes):
        curr_dt = dt_operacao
        if i > 0:
            if modo_repeticao == "parcelado" or frequencia == "mensal":
                curr_dt = dt_operacao + relativedelta(months=i)
            elif frequencia == "semanal":
                curr_dt = dt_operacao + timedelta(weeks=i)
            elif frequencia == "anual":
                curr_dt = dt_operacao + relativedelta(years=i)
        
        curr_parcela = None
        if modo_repeticao == "parcelado":
            curr_parcela = f"{i+1:03d}.{num_parcelas:03d}"
        elif parcela_str:
            curr_parcela = parcela_str

        # Define data_efetivado apenas para o primeiro da série se for o caso
        curr_efetivado = efetivado if i == 0 else 0
        curr_dt_efetivado = dt_efetivado if i == 0 else None

        op_saida = Operacao(
            operacoes_data_lancamento=curr_dt,
            operacoes_descricao=descricao,
            operacoes_conta=conta,
            operacoes_valor=-valor_final,
            operacoes_tipo="4",
            operacoes_fatura=get_or_create_fatura(db, conta, curr_dt) if fat_id else None,
            operacoes_parcela=curr_parcela,
            operacoes_efetivado=curr_efetivado,
            operacoes_data_efetivado=curr_dt_efetivado,
            operacoes_validacao=1,
            operacoes_grupo_id=grupo_id
        )
        op_entrada = Operacao(
            operacoes_data_lancamento=curr_dt,
            operacoes_descricao=descricao,
            operacoes_conta=conta_destino,
            operacoes_valor=valor_final,
            operacoes_tipo="4",
            operacoes_fatura=get_or_create_fatura(db, conta_destino, curr_dt) if fat_id else None,
            operacoes_parcela=curr_parcela,
            operacoes_efetivado=curr_efetivado,
            operacoes_data_efetivado=curr_dt_efetivado,
            operacoes_validacao=1,
            operacoes_grupo_id=grupo_id
        )
        db.add(op_saida); db.add(op_entrada); db.flush()
        op_saida.operacoes_transf_rel = op_entrada.operacoes_id
        op_entrada.operacoes_transf_rel = op_saida.operacoes_id

        if i == 0:
            log_evento(db, "INSERT", "TRANSFERÊNCIA", op_saida.operacoes_id, f"Série '{descricao}' compartilhada no Grupo {grupo_id}", sessao.get("id"))
    
    db.commit()
    
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
    frequencia: Optional[str] = Form(default="mensal"), #
    ocorrencias: Optional[int] = Form(default=12), #
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    # Limpa formatação BRL (1.800,00 -> 1800.00)
    valor_limpo = valor.replace(".", "").replace(",", ".")
    valor_float = float(valor_limpo)
    valor_final = -abs(valor_float) if tipo == 3 else abs(valor_float)

    # Converte categoria e fatura para int ou None (lidando com strings vazias do form)
    cat_id = int(categoria) if categoria and categoria.strip() else None
    fat_id = int(fatura) if fatura and fatura.strip() else None

    # Se for cartão, a conta da operação deve ser o próprio cartão (fat_id)
    if fat_id:
        conta = fat_id

    parcela_str = None
    if parcela_atual and parcela_total:
        parcela_str = f"{parcela_atual:03d}.{parcela_total:03d}"

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

    for i in range(repeticoes):
        curr_dt = dt_operacao
        if i > 0:
            if modo_repeticao == "parcelado" or frequencia == "mensal":
                curr_dt = dt_operacao + relativedelta(months=i)
            elif frequencia == "semanal":
                curr_dt = dt_operacao + timedelta(weeks=i)
            elif frequencia == "anual":
                curr_dt = dt_operacao + relativedelta(years=i)
        
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
            operacoes_valor=valor_final,
            operacoes_tipo=tipo,
            operacoes_categoria=cat_id,
            operacoes_fatura=curr_fat_id,
            operacoes_parcela=curr_parcela,
            operacoes_efetivado=curr_efetivado,
            operacoes_data_efetivado=curr_dt_efetivado,
            operacoes_validacao=1,
            operacoes_grupo_id=grupo_id
        )
        db.add(op)
        
        if curr_fat_id:
            db.query(FaturaCartao).filter(FaturaCartao.fatura_id == curr_fat_id).update({FaturaCartao.valor_total: FaturaCartao.valor_total + valor_final})
        
        db.flush()
        if i == 0:
            log_evento(db, "INSERT", f"LANÇAMENTO {tipo}", op.operacoes_id, f"Início de série '{descricao}' no Grupo {grupo_id}", sessao.get("id"))

    db.commit()
    
    redirecionar = next_url or request.headers.get("referer") or f"/extrato?c={conta}"
    return RedirectResponse(url=redirecionar, status_code=303)


@router.post("/efetivar/{op_id}")
async def efetivar(request: Request, op_id: int, db=Depends(get_db), sessao=Depends(require_login)):
    from datetime import datetime
    op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if op:
        agora = datetime.now()
        op.operacoes_efetivado = 1
        op.operacoes_data_efetivado = agora
        
        # Se for transferência, efetiva o outro lado também
        if op.operacoes_transf_rel:
            rel = db.query(Operacao).filter(Operacao.operacoes_id == op.operacoes_transf_rel).first()
            if rel:
                rel.operacoes_efetivado = 1
                rel.operacoes_data_efetivado = agora
                log_evento(db, "UPDATE", "OPERACAO", rel.operacoes_id, f"Efetivado lado relacionado da transferência '{rel.operacoes_descricao}'", sessao.get("id"))
        
        log_evento(db, "UPDATE", "OPERACAO", op.operacoes_id, f"Efetivado lançamento '{op.operacoes_descricao}'", sessao.get("id"))
        
        db.commit()
    return RedirectResponse(url=request.headers.get("referer", "/dashboard"), status_code=303)


@router.post("/deletar/{op_id}")
async def deletar(request: Request, op_id: int, db=Depends(get_db), sessao=Depends(require_login)):
    op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if op:
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

    log_evento(db, "UPDATE", "OPERACAO", op_id, detalhes, sessao.get("id"))
    
    # IMPORTANTE: Captura a data original para a lógica de cascata ANTES de aplicar a edição
    old_date = op.operacoes_data_lancamento
    grupo_id = op.operacoes_grupo_id

    _aplicar_edicao(op)
    op.operacoes_efetivado = efetivado
    if efetivado and data_efetivado:
        op.operacoes_data_efetivado = datetime.fromisoformat(data_efetivado)
    elif efetivado and not op.operacoes_data_efetivado:
        op.operacoes_data_efetivado = datetime.now()
    elif not efetivado:
        op.operacoes_data_efetivado = None

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

    db.commit()

    redirecionar = next_url or request.headers.get("referer") or "/dashboard"
    return RedirectResponse(url=redirecionar, status_code=303)


@router.post("/editar-transferencia/{op_id}")
async def editar_transferencia(
    request: Request,
    op_id: int,
    descricao: str = Form(...),
    conta_origem: int = Form(...),
    conta_destino: int = Form(...),
    valor: str = Form(...),
    data: str = Form(...),
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

    detalhes = f"Transferência editada: '{descricao}' R$ {valor_float} de conta {conta_origem} -> {conta_destino}"

    if op_saida:
        op_saida.operacoes_descricao = descricao
        op_saida.operacoes_conta = conta_origem
        op_saida.operacoes_valor = -abs(valor_float)
        op_saida.operacoes_data_lancamento = dt_operacao
        log_evento(db, "UPDATE", "OPERACAO", op_saida.operacoes_id, detalhes, sessao.get("id"))

    if op_entrada:
        op_entrada.operacoes_descricao = descricao
        op_entrada.operacoes_conta = conta_destino
        op_entrada.operacoes_valor = abs(valor_float)
        op_entrada.operacoes_data_lancamento = dt_operacao
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
    
    return RedirectResponse(url=request.headers.get("referer", "/dashboard"), status_code=303)
