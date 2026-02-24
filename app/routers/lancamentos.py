from datetime import date, datetime, timedelta
from typing import Optional
from calendar import monthrange

from fastapi import APIRouter, Request, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
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

    op_saida = Operacao(
        operacoes_data_lancamento=dt_operacao,
        operacoes_descricao=descricao,
        operacoes_conta=conta,
        operacoes_valor=-valor_final,
        operacoes_tipo="4",
        operacoes_fatura=final_fat_id,
        operacoes_parcela=parcela_str,
        operacoes_efetivado=0,
        operacoes_validacao=1,
    )
    op_entrada = Operacao(
        operacoes_data_lancamento=dt_operacao,
        operacoes_descricao=descricao,
        operacoes_conta=conta_destino,
        operacoes_valor=valor_final,
        operacoes_tipo="4",
        operacoes_fatura=final_fat_id,
        operacoes_parcela=parcela_str,
        operacoes_efetivado=0,
        operacoes_validacao=1,
    )
    db.add(op_saida); db.add(op_entrada); db.flush()
    op_saida.operacoes_transf_rel = op_entrada.operacoes_id
    op_entrada.operacoes_transf_rel = op_saida.operacoes_id

    # Log da inserção
    log_evento(db, "INSERT", "TRANSFERÊNCIA", op_saida.operacoes_id, f"'{descricao}' de <b>R$ {valor_final}</b> de <b>{nome_origem}</b> para <b>{nome_destino}</b>", sessao.get("id"))
    
    db.commit()
    
    redirecionar = next_url or request.headers.get("referer") or f"/extrato?c={conta}"
    return RedirectResponse(url=redirecionar, status_code=303)




@router.post("/inserir")
async def inserir_lancamento(
    request: Request,
    descricao: str = Form(...),
    conta: Optional[int] = Form(default=None),
    valor: str = Form(...),
    tipo: int = Form(...),
    data: str = Form(...),
    categoria: Optional[str] = Form(default=None),
    fatura: Optional[str] = Form(default=None),
    parcela_atual: Optional[int] = Form(default=None),
    parcela_total: Optional[int] = Form(default=None),
    next_url: Optional[str] = Form(default=None),
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

    op = Operacao(
        operacoes_data_lancamento=dt_operacao,
        operacoes_descricao=descricao,
        operacoes_conta=conta,
        operacoes_valor=valor_final,
        operacoes_tipo=tipo,
        operacoes_categoria=cat_id,
        operacoes_fatura=final_fat_id,
        operacoes_parcela=parcela_str,
        operacoes_efetivado=0,
        operacoes_validacao=1,
    )
    db.add(op);
    
    if final_fat_id:
        # Usamos um update atômico no banco de dados. 
        # Isso evita que dois usuários atualizando ao mesmo tempo sobrescrevam um ao outro.
        db.query(FaturaCartao).filter(FaturaCartao.fatura_id == final_fat_id).update({FaturaCartao.valor_total: FaturaCartao.valor_total + valor_final})
            
    db.flush()

    # Log da inserção
    log_evento(db, "INSERT", "OPERACAO", op.operacoes_id, f"Lançamento '{descricao}' de R$ {valor_final}", sessao.get("id"))
    
    db.commit()
    
    redirecionar = next_url or request.headers.get("referer") or f"/extrato?c={conta}"
    return RedirectResponse(url=redirecionar, status_code=303)


@router.post("/efetivar/{op_id}")
async def efetivar(request: Request, op_id: int, db=Depends(get_db), sessao=Depends(require_login)):
    from datetime import datetime
    op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if op:
        op.operacoes_efetivado = 1
        op.operacoes_data_efetivado = datetime.now()
        
        # Log da efetivação
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


@router.post("/editar/{op_id}")
async def editar(
    request: Request,
    op_id: int,
    descricao: str = Form(...),
    conta: int = Form(...),
    valor: str = Form(...),
    tipo: int = Form(...),
    data: str = Form(...),
    categoria: Optional[int] = Form(default=None),
    next_url: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    op = db.query(Operacao).filter(Operacao.operacoes_id == op_id).first()
    if op:
        # Limpa formatação BRL
        valor_limpo = valor.replace(".", "").replace(",", ".")
        valor_float = float(valor_limpo)
        valor_final = -abs(valor_float) if tipo == 3 else abs(valor_float)
        
        detalhes = f"Editado: '{op.operacoes_descricao}' -> '{descricao}'. R$ {op.operacoes_valor} -> R$ {valor_final}"
        
        op.operacoes_descricao = descricao
        op.operacoes_conta = conta
        op.operacoes_valor = valor_final
        op.operacoes_tipo = tipo
        op.operacoes_data_lancamento = date.fromisoformat(data)
        op.operacoes_categoria = categoria
        
        # Log da edição
        log_evento(db, "UPDATE", "OPERACAO", op_id, detalhes, sessao.get("id"))
        
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
