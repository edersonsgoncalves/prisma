"""routers/extrato.py — Extrato de conta e extrato geral."""
from pathlib import Path
from datetime import date, timedelta
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
    tipo_lancamento: Optional[str] = Query(None, description="Tipo de Lançamento"),
    cat: Optional[str] = Query(None, description="ID da categoria"),
    status: Optional[str] = Query(None, description="Status (0=pendente, 1=efetivado)"),
    sessao: dict = Depends(require_login),
    db: Session = Depends(get_db),
):
    # Converte strings vazias vindas do template para None
    c_id = int(c) if c and c.strip() else None
    cat_id = int(cat) if cat and cat.strip() else None
    status_id = int(status) if status and status.strip() else None
    tipo_lancamento_id = int(tipo_lancamento) if tipo_lancamento and tipo_lancamento.strip() else None

    # Query base
    query = db.query(Operacao).options(
        joinedload(Operacao.conta),
        joinedload(Operacao.transf_rel_obj).joinedload(Operacao.conta)
    )
    
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
    
    # Filtro do Tipo de Lançamento
    if tipo_lancamento_id is not None:
        query = query.filter(Operacao.operacoes_tipo == tipo_lancamento_id)
    
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
    operacoes = query.order_by(is_atrasado, Operacao.operacoes_data_efetivado, Operacao.operacoes_data_lancamento, Operacao.operacoes_id).all()

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

    # Dados para os selects do filtro
    # Ordenar por: (Se tem pai, usa ID do pai, senão usa próprio ID) para agrupar filhos logo após o pai.
    categorias = db.query(Categoria).order_by(
        func.coalesce(Categoria.categorias_pai_id, Categoria.categorias_id),
        Categoria.categorias_pai_id.isnot(None), 
        Categoria.categorias_nome
    ).all()
    contas_todas = db.query(ContaBancaria).filter(ContaBancaria.tipo_conta != 4).order_by(ContaBancaria.nome_conta.asc()).all()
    
    mapa_tipo_conta = {
        0: "Pagamento de Fatura",
        1: "Corrente",
        2: "Espécie",
        3: "Financeiro",
        5: "Investimento",
        6: "Financeiro"
    }

    return templates.TemplateResponse("extrato.html", {
        "request": request,
        "sessao": sessao,
        "conta_id": c_id,
        "operacoes": operacoes,
        "saldo_anterior": saldo_anterior,
        "categorias": categorias,
        "contas_todas": contas_todas,
        "tipos_conta_map": mapa_tipo_conta,
        "mes": m,
        "ano": y,
        "cat_id": cat_id,
        "status_id": status_id,
        "tipo_lancamento_id": tipo_lancamento_id,
        "hoje": date.today(),
        "data_saldo_anterior": date(y, m, 1) - timedelta(days=1) if m and y else None,
    })


@router.get("/conta/{conta_id}", response_class=HTMLResponse)
async def extrato_por_conta(
    request: Request,
    conta_id: int,
    m: int = Query(default=date.today().month, description="Mês"),
    y: int = Query(default=date.today().year, description="Ano"),
    tipo_lancamento: Optional[str] = Query(None, description="Tipo de Lançamento"),
    sessao: dict = Depends(require_login),
    db: Session = Depends(get_db),
):
    tipo_lancamento_id = int(tipo_lancamento) if tipo_lancamento and tipo_lancamento.strip() else None
    conta = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta_id).first()
    if not conta:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/extrato", status_code=303)

    hoje = date.today()
    is_hoje_periodo = (m == hoje.month and y == hoje.year)

    from sqlalchemy import and_, or_
    query = db.query(Operacao).options(
        joinedload(Operacao.conta),
        joinedload(Operacao.transf_rel_obj).joinedload(Operacao.conta)
    ).filter(
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

    # Filtro do Tipo de Lançamento
    if tipo_lancamento_id is not None:
        query = query.filter(Operacao.operacoes_tipo == tipo_lancamento_id)

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
        "categorias": categorias,
        "contas_todas": contas_todas,
        "mes": m,
        "ano": y,
        "tipo_lancamento_id": tipo_lancamento_id,
        "hoje": date.today(),
        "data_saldo_anterior": date(y, m, 1) - timedelta(days=1) if m and y else None,
    })

