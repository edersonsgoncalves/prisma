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
from app.helpers import formata_moeda_brl, mostra_data, cor_valor, mes_por_extenso, formata_parcela

# Funções utilitárias compartilhadas
from .lancamentos_utils import log_evento, get_or_create_fatura, recalcular_total_fatura

# Routers especializados
from .lancamentos_transfers import inserir_transferencia, editar_transferencia


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


# Funções auxiliares movidas para lancamentos_utils.py


@router.get("/listar")
async def listar_lancamentos(
    request: Request,
    limit: int = Query(300),
    conta_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login)
    ):
    from sqlalchemy import desc
    query = db.query(Operacao)
    if conta_id:
        query = query.filter(Operacao.operacoes_conta == conta_id)
    ops = query.order_by(desc(Operacao.operacoes_data_lancamento)).limit(limit).all()
    resultado = []
    for op in ops:
        tipo_dc = {1: "C", 3: "D", 4: "T"}.get(op.operacoes_tipo, "D")
        resultado.append({
            "operacoes_id": op.operacoes_id,
            "operacoes_descricao": op.operacoes_descricao,
            "operacoes_valor": float(op.operacoes_valor or 0),
            "operacoes_data_lancamento": op.operacoes_data_lancamento.isoformat() if op.operacoes_data_lancamento else None,
            "operacoes_tipo": op.operacoes_tipo,
            "tipo": tipo_dc,
            "operacoes_efetivado": op.operacoes_efetivado or 0,
            "ofx_fitid": getattr(op, "operacoes_fitid", None)
        })
    return JSONResponse(content={"operacoes": resultado})


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


# Rotas de transferência movidas para lancamentos_transfers.py

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
    # sinalizamos que haverá fatura sem alterar `conta` nem `fat_id` —
    # get_or_create_fatura resolve o ID correto mais adiante com base em `conta`.
    acct_obj = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta).first()
    if acct_obj and acct_obj.tipo_conta == 4 and not fat_id:
        # Cartão sem fatura explícita no form: marca que precisa de fatura.
        # Usamos True como flag; o bloco abaixo chama get_or_create_fatura com `conta`.
        fat_id = True  # flag — não é um ID real de fatura

    parcela_str = None
    if parcela_atual and parcela_total:
        parcela_str = f"{parcela_atual:03d}.{parcela_total:03d}"

    # Conversão de destinos e adicionais (podem vir como strings vazias)
    conta_destino_id = int(conta_destino) if conta_destino and conta_destino.strip() and conta_destino != "-1" else None
    add_id = int(adicional_id) if adicional_id and adicional_id.strip() and adicional_id != "-1" else None

    dt_operacao = date.fromisoformat(data)

    # Lógica de Fatura Inteligente:
    # fat_id pode ser: None (sem fatura), True (flag: cartão sem fatura explícita), ou int (fatura_id real do form).
    # Em qualquer caso com fatura, passamos `conta` (ID do cartão) para get_or_create_fatura.
    if fat_id:
        final_fat_id = get_or_create_fatura(db, conta, dt_operacao)
    else:
        final_fat_id = None

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
        if is_repetir and modo_repeticao == "parcelado":
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


# Rota movida para lancamentos_faturas.py

@router.post("/efetivar/{op_id}")
async def efetivar(request: Request, op_id: int, db=Depends(get_db), sessao=Depends(require_login)):
    op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if op:
        agora = datetime.now()
        op.operacoes_efetivado = 1
        op.operacoes_data_efetivado = agora  # registra quando de fato foi conciliado

        # Se for transferência, efetiva o outro lado também
        if op.operacoes_transf_rel:
            rel = db.query(Operacao).filter(Operacao.operacoes_id == op.operacoes_transf_rel).first()
            if rel:
                rel.operacoes_efetivado = 1
                rel.operacoes_data_efetivado = agora
                log_evento(db, "UPDATE", "OPERACAO", rel.operacoes_id, f"Efetivado lado relacionado da transferência '{rel.operacoes_descricao}'", sessao.get("id"))

        log_evento(db, "UPDATE", "OPERACAO", op.operacoes_id, f"Efetivado lançamento '{op.operacoes_descricao}'", sessao.get("id"))

        db.commit()
        if op.operacoes_fatura: recalcular_total_fatura(db, op.operacoes_fatura)
    return RedirectResponse(url=request.headers.get("referer", "/dashboard"), status_code=303)

@router.post("/deletar/serie")
async def deletar_serie(
    request: Request,
    operacao_id: int = Form(...),
    modo: str = Form(...),
    db=Depends(get_db),
    sessao=Depends(require_login),
):
    """
    Remove lançamentos de uma série parcelada.

    modo="only" → apaga apenas este lançamento.
    modo="all"  → apaga este e todos os lançamentos FUTUROS do mesmo grupo
                  (filtra por data >= data do lançamento clicado).
                  Nunca toca em parcelas já passadas / já pagas.

    Segurança: sem grupo_id, o modo "all" é abortado — jamais faz busca
    por descrição/valor, que causava deleções em massa não intencionais.
    """
    _redirect = lambda: RedirectResponse(
        url=request.headers.get("referer", "/dashboard"), status_code=303
    )

    op = db.query(Operacao).filter(Operacao.operacoes_id == operacao_id).first()
    if not op:
        return _redirect()

    faturas_recalc: set = set()
    if op.operacoes_fatura:
        faturas_recalc.add(op.operacoes_fatura)

    # ------------------------------------------------------------------ #
    #  MODO: apaga somente este lançamento                                 #
    # ------------------------------------------------------------------ #
    if modo == "only":
        log_evento(
            db, "DELETE", "OPERACAO", op.operacoes_id,
            f"Removido lançamento avulso '{op.operacoes_descricao[:40]}'",
            sessao.get("id"),
        )
        db.delete(op)
        db.commit()
        for f_id in faturas_recalc:
            recalcular_total_fatura(db, f_id)
        return _redirect()

    # ------------------------------------------------------------------ #
    #  MODO: apaga este e todos os futuros do mesmo grupo                  #
    # ------------------------------------------------------------------ #
    elif modo == "all":
        # Trava de segurança: sem grupo_id não há como identificar a série
        # com precisão; abortar é mais seguro do que apagar por nome/valor.
        if not op.operacoes_grupo_id:
            log_evento(
                db, "DELETE", "OPERACAO", op.operacoes_id,
                f"Deleção de série abortada: lançamento sem grupo_id "
                f"('{op.operacoes_descricao[:40]}')",
                sessao.get("id"),
            )
            db.commit()
            return _redirect()

        # Busca apenas as parcelas deste grupo com data >= data do clique
        futuros = (
            db.query(Operacao)
            .filter(
                Operacao.operacoes_grupo_id == op.operacoes_grupo_id,
                Operacao.operacoes_data_lancamento >= op.operacoes_data_lancamento,
            )
            .all()
        )

        parcelas_removidas = 0
        for tl in futuros:
            if tl.operacoes_fatura:
                faturas_recalc.add(tl.operacoes_fatura)
            db.delete(tl)
            parcelas_removidas += 1

        log_evento(
            db, "DELETE", "OPERACAO", op.operacoes_id,
            f"Removidas {parcelas_removidas} parcela(s) futuras de "
            f"'{op.operacoes_descricao[:40]}' "
            f"(grupo {op.operacoes_grupo_id}, a partir de {op.operacoes_data_lancamento})",
            sessao.get("id"),
        )
        db.commit()
        for f_id in faturas_recalc:
            recalcular_total_fatura(db, f_id)
        return _redirect()

    # modo desconhecido — não faz nada
    return _redirect()


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
                rel_fat_id = rel.operacoes_fatura  # captura antes de deletar
                log_evento(db, "DELETE", "OPERACAO", rel.operacoes_id, f"Removido lançamento relacionado (Transferência) '{rel.operacoes_descricao}'", sessao.get("id"))
                db.delete(rel)

        log_evento(db, "DELETE", "OPERACAO", op_id, f"Removido lançamento '{desc_backup}'", sessao.get("id"))

        db.delete(op)
        db.commit()

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
    adicional_id: Optional[str] = Form(default=None),
    nova_parcela_total: Optional[str] = Form(default=None),
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
    add_id = int(adicional_id) if adicional_id and str(adicional_id).strip() else None
    nova_parcela_total_int = int(nova_parcela_total) if nova_parcela_total and str(nova_parcela_total).strip() else None

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
        operacao.operacoes_adicional_id = add_id

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
    if nova_parcela_total_int and op.operacoes_parcela:
        p_sep = "/" if "/" in str(op.operacoes_parcela) else "."
        atual = int(str(op.operacoes_parcela).split(p_sep)[0])
        op.operacoes_parcela = f"{atual:03d}{p_sep}{nova_parcela_total_int:03d}"

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


# Rota movida para lancamentos_transfers.py


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