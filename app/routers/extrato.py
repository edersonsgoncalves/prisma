"""routers/extrato.py — Extrato de conta e extrato geral."""
from pathlib import Path
from datetime import date
from typing import Optional
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.templates import templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_, or_, extract, case

from app.database import get_db
from app.auth import require_login
from app.models import Operacao, ContaBancaria, Categoria

router = APIRouter(prefix="/extrato", tags=["extrato"])
BASE_DIR = Path(__file__).resolve().parent.parent
# BASE_DIR = Path(__file__).resolve().parent.parent
# templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@router.get("", response_class=HTMLResponse)
async def extrato_unificado(
    request: Request,
    c: Optional[str] = Query(None, description="ID da conta"),
    m: int = Query(default=date.today().month, description="Mês"),
    y: int = Query(default=date.today().year, description="Ano"),
    cat: Optional[str] = Query(None, description="ID da categoria"),
    status: Optional[str] = Query(None, description="Status (0=pendente, 1=efetivado)"),
    sessao: dict = Depends(require_login),
    db: Session = Depends(get_db),
):
    # Converte strings vazias vindas do template para None
    c_id = int(c) if c and c.strip() else None
    cat_id = int(cat) if cat and cat.strip() else None
    status_id = int(status) if status and status.strip() else None

    # Query base
    query = db.query(Operacao).options(joinedload(Operacao.conta))
    
    # Filtros de data e status "especial" para o mês atual
    hoje = date.today()
    is_hoje_periodo = (m == hoje.month and y == hoje.year)

    if is_hoje_periodo:
        # No mês atual, mostramos:
        # 1. Tudo o que é do mês atual (efetivado ou não)
        # 2. Tudo o que está pendente de meses PASSADOS (atrasados)
        query = query.filter(
            Operacao.operacoes_validacao == 1,
            or_(
                # Item do mês atual
                and_(
                    extract('month', Operacao.operacoes_data_lancamento) == m,
                    extract('year', Operacao.operacoes_data_lancamento) == y
                ),
                # Item pendente do passado
                and_(
                    func.coalesce(Operacao.operacoes_efetivado, 0) != 1,
                    Operacao.operacoes_data_lancamento < date(y, m, 1)
                )
            )
        )
    else:
        # Em outros meses, mostramos apenas o que é daquele mês específico
        query = query.filter(
            extract('month', Operacao.operacoes_data_lancamento) == m,
            extract('year', Operacao.operacoes_data_lancamento) == y,
            Operacao.operacoes_validacao == 1
        )

    # Filtro de Conta
    if c_id:
        query = query.filter(Operacao.operacoes_conta == c_id)
    
    # Filtro de Categoria
    if cat_id:
        # Busca a categoria e suas subcategorias recursivamente (apenas 1 nível por enquanto 
        # já que a migração foi plana, mas a lógica permite expansão)
        subcat_ids = db.query(Categoria.categorias_id).filter(Categoria.categorias_pai_id == cat_id).all()
        ids_filtro = [cat_id] + [s[0] for s in subcat_ids]
        query = query.filter(Operacao.operacoes_categoria.in_(ids_filtro))
    
    # Filtro de Status
    if status_id is not None:
        query = query.filter(Operacao.operacoes_efetivado == status_id)

    # Ordenação: Efetivados/Normais primeiro, Atrasados (vencidos) por último
    # Conforme solicitado: "vencidos... aparecessem exclusivamente no final da lista"
    hoje = date.today()
    is_atrasado = case(
        (and_(
            func.coalesce(Operacao.operacoes_efetivado, 0) != 1, 
            Operacao.operacoes_data_lancamento < hoje
        ), 1),
        else_=0
    )
    operacoes = query.order_by(is_atrasado, Operacao.operacoes_data_lancamento, Operacao.operacoes_id).all()

    # Cálculo do saldo anterior (acumulado antes do mês)
    # Deve considerar apenas a conta selecionada OU todas se nenhuma selecionada
    sa_query = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_data_lancamento < date(y, m, 1),
        Operacao.operacoes_efetivado == 1,
        Operacao.operacoes_validacao == 1,
    )
    if c:
        sa_query = sa_query.filter(Operacao.operacoes_conta == c)
    
    saldo_anterior = sa_query.scalar() or 0

    # IDs das contas destino de transferências
    ids_transf = [op.operacoes_transf_rel for op in operacoes if op.operacoes_transf_rel]
    contas_transf = {}
    if ids_transf:
        ops_transf = (
            db.query(Operacao.operacoes_id, ContaBancaria.nome_conta)
            .join(ContaBancaria, Operacao.operacoes_id == ContaBancaria.conta_id) # Corrigido: era idcontas_bancarias
            .filter(Operacao.operacoes_id.in_(ids_transf))
            .all()
        )
        # Nota: Ajustei o JOIN acima para usar conta_id se necessário, 
        # mas na verdade o operacoes_transf_rel aponta para o ID da OPERAÇÃO relacionada, 
        # e de lá pegamos a conta. A query original parecia buscar o nome da conta 
        # da operação relacionada. Vamos manter a lógica original corrigida para os nomes novos.
        
        # A query original era: .join(ContaBancaria, Operacao.operacoes_conta == ContaBancaria.idcontas_bancarias)
        # O que não faz sentido se estamos filtrando por Operacao.operacoes_id.in_(ids_transf).
        # Vamos refazer para pegar o nome da conta associada a cada operação de transferência relacionada.
        ops_transf = (
            db.query(Operacao.operacoes_id, ContaBancaria.nome_conta)
            .join(ContaBancaria, Operacao.operacoes_conta == ContaBancaria.conta_id)
            .filter(Operacao.operacoes_id.in_(ids_transf))
            .all()
        )
        contas_transf = {op_id: nome for op_id, nome in ops_transf}

    # Dados para os selects do filtro
    # Ordenar por: (Se tem pai, usa ID do pai, senão usa próprio ID) para agrupar filhos logo após o pai.
    categorias = db.query(Categoria).order_by(
        func.coalesce(Categoria.categorias_pai_id, Categoria.categorias_id),
        Categoria.categorias_pai_id.isnot(None), 
        Categoria.categorias_nome
    ).all()
    contas_todas = db.query(ContaBancaria).filter(ContaBancaria.tipo_conta != 4).order_by(ContaBancaria.nome_conta.asc()).all()

    return templates.TemplateResponse("extrato.html", {
        "request": request,
        "sessao": sessao,
        "conta_id": c,
        "operacoes": operacoes,
        "saldo_anterior": saldo_anterior,
        "contas_transf": contas_transf,
        "categorias": categorias,
        "contas_todas": contas_todas,
        "mes": m,
        "ano": y,
        "cat_id": cat,
        "status_id": status,
        "hoje": date.today(),
    })


@router.get("/conta/{conta_id}", response_class=HTMLResponse)
async def extrato_por_conta(
    request: Request,
    conta_id: int,
    m: int = Query(default=date.today().month, description="Mês"),
    y: int = Query(default=date.today().year, description="Ano"),
    sessao: dict = Depends(require_login),
    db: Session = Depends(get_db),
):
    conta = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta_id).first()
    if not conta:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/extrato", status_code=303)

    hoje = date.today()
    is_hoje_periodo = (m == hoje.month and y == hoje.year)

    from sqlalchemy import and_, or_
    query = db.query(Operacao).options(joinedload(Operacao.conta)).filter(
        Operacao.operacoes_conta == conta_id,
        Operacao.operacoes_validacao == 1,
    )

    if is_hoje_periodo:
        query = query.filter(
            or_(
                and_(
                    extract('month', Operacao.operacoes_data_lancamento) == m,
                    extract('year', Operacao.operacoes_data_lancamento) == y,
                ),
                and_(
                    func.coalesce(Operacao.operacoes_efetivado, 0) != 1,
                    Operacao.operacoes_data_lancamento < date(y, m, 1),
                )
            )
        )
    else:
        query = query.filter(
            extract('month', Operacao.operacoes_data_lancamento) == m,
            extract('year', Operacao.operacoes_data_lancamento) == y,
        )

    is_atrasado = case(
        (and_(
            func.coalesce(Operacao.operacoes_efetivado, 0) != 1,
            Operacao.operacoes_data_lancamento < hoje,
        ), 1),
        else_=0
    )
    operacoes = query.order_by(is_atrasado, Operacao.operacoes_data_lancamento, Operacao.operacoes_id).all()

    # Saldo anterior
    saldo_anterior = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_conta == conta_id,
        Operacao.operacoes_efetivado == 1,
        Operacao.operacoes_validacao == 1,
        Operacao.operacoes_data_lancamento < date(y, m, 1),
    ).scalar() or 0

    # Contas destino de transferências
    ids_transf = [op.operacoes_transf_rel for op in operacoes if op.operacoes_transf_rel]
    contas_transf = {}
    if ids_transf:
        ops_transf = (
            db.query(Operacao.operacoes_id, ContaBancaria.nome_conta)
            .join(ContaBancaria, Operacao.operacoes_conta == ContaBancaria.conta_id)
            .filter(Operacao.operacoes_id.in_(ids_transf))
            .all()
        )
        contas_transf = {op_id: nome for op_id, nome in ops_transf}

    categorias = db.query(Categoria).order_by(
        func.coalesce(Categoria.categorias_pai_id, Categoria.categorias_id),
        Categoria.categorias_pai_id.isnot(None),
        Categoria.categorias_nome
    ).all()
    contas_todas = db.query(ContaBancaria).filter(ContaBancaria.tipo_conta != 4).all()

    return templates.TemplateResponse("extrato_conta.html", {
        "request": request,
        "sessao": sessao,
        "conta": conta,
        "operacoes": operacoes,
        "saldo_anterior": saldo_anterior,
        "contas_transf": contas_transf,
        "categorias": categorias,
        "contas_todas": contas_todas,
        "mes": m,
        "ano": y,
        "hoje": date.today(),
    })

