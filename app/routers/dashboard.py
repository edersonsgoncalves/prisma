"""routers/dashboard.py — Página principal (substitui index.php)"""
from pathlib import Path
from datetime import date
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, extract, case, text

from app.database import get_db
from app.auth import require_login
from app.models import Operacao, ContaBancaria, FaturaCartao, Categoria

router = APIRouter(tags=["dashboard"])
from app.templates import templates


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    mes: int = Query(default=date.today().month),
    ano: int = Query(default=date.today().year),
    sessao: dict = Depends(require_login),
    db: Session = Depends(get_db),
):
    # 1. Busca as contas válidas para o saldo (Filtro corrigido com None)
    contas_validas_query = db.query(ContaBancaria).filter(
        ContaBancaria.tipo_conta != 4,
        or_(
            ContaBancaria.contas_desconsiderar_saldo == None,
            ContaBancaria.contas_desconsiderar_saldo == 0
        )
    ).order_by(ContaBancaria.nome_conta.asc())
    contas = contas_validas_query.all()
    ids_contas_validas = [c.conta_id for c in contas]

    # 2. OTIMIZAÇÃO: Busca TODOS os saldos de uma vez só em vez de usar um loop for
    # Isso reduz drasticamente o tempo de carregamento
    saldos_query = db.query(
        Operacao.operacoes_conta,
        func.sum(Operacao.operacoes_valor).label("total")
    ).filter(
        Operacao.operacoes_conta.in_(ids_contas_validas),
        Operacao.operacoes_efetivado == 1,
        Operacao.operacoes_validacao == 1
    ).group_by(Operacao.operacoes_conta).all()

    # Transforma o resultado em um dicionário {id_conta: valor}
    saldos = {row.operacoes_conta: row.total for row in saldos_query}
    
    # Garante que contas sem operações apareçam com saldo 0
    for c_id in ids_contas_validas:
        if c_id not in saldos:
            saldos[c_id] = 0
            
    total_saldos = sum(saldos.values())

    # 3. Receitas e Despesas (Queries permanecem as mesmas, apenas garantindo consistência)
    receitas_efetivadas = db.query(func.sum(Operacao.operacoes_valor)).filter(
        extract('month', Operacao.operacoes_data_lancamento) == mes,
        extract('year', Operacao.operacoes_data_lancamento) == ano,
        Operacao.operacoes_tipo == 1,
        Operacao.operacoes_efetivado == 1,
        Operacao.operacoes_validacao == 1,
    ).scalar() or 0

    despesas_totais = db.query(func.sum(Operacao.operacoes_valor)).filter(
        extract('month', Operacao.operacoes_data_lancamento) == mes,
        extract('year', Operacao.operacoes_data_lancamento) == ano,
        Operacao.operacoes_tipo == 3,
        Operacao.operacoes_validacao == 1,
    ).scalar() or 0

    despesas_efetivadas = db.query(func.sum(Operacao.operacoes_valor)).filter(
        extract('month', Operacao.operacoes_data_lancamento) == mes,
        extract('year', Operacao.operacoes_data_lancamento) == ano,
        Operacao.operacoes_tipo == 3,
        Operacao.operacoes_efetivado == 1,
        Operacao.operacoes_validacao == 1,
    ).scalar() or 0

    # Lançamentos pendentes do mês atual para o card (em valor)
    despesas_pendentes = db.query(func.sum(Operacao.operacoes_valor)).filter(
        extract('month', Operacao.operacoes_data_lancamento) == mes,
        extract('year', Operacao.operacoes_data_lancamento) == ano,
        Operacao.operacoes_tipo == 3,
        Operacao.operacoes_efetivado == 0,
        Operacao.operacoes_validacao == 1,
    ).scalar() or 0

    # Gastos por Categoria (Barras Empilhadas: Planejado vs Efetivado)
    from sqlalchemy.orm import aliased
    Pai = aliased(Categoria)
    
    gastos_cat_query = db.query(
        case(
            (Categoria.categorias_pai_id.isnot(None), func.concat(Pai.categorias_nome, " > ", Categoria.categorias_nome)),
            else_=Categoria.categorias_nome
        ).label("nome_completo"),
        func.sum(Operacao.operacoes_valor).label("total"),
        func.sum(case((Operacao.operacoes_efetivado == 1, Operacao.operacoes_valor), else_=0)).label("efetivado")
    ).join(Operacao, Operacao.operacoes_categoria == Categoria.categorias_id)\
     .outerjoin(Pai, Categoria.categorias_pai_id == Pai.categorias_id)\
     .filter(
        extract('month', Operacao.operacoes_data_lancamento) == mes,
        extract('year', Operacao.operacoes_data_lancamento) == ano,
        Operacao.operacoes_tipo == 3,
        Operacao.operacoes_validacao == 1,
    ).group_by(text("nome_completo")).all()

    gastos_por_categoria = [
        {"nome": g.nome_completo, "total": float(abs(g.total)), "efetivado": float(abs(g.efetivado))}
        for g in gastos_cat_query
    ]

    # Faturas não fechadas
    faturas_abertas = db.query(FaturaCartao).filter(
        FaturaCartao.fechado == 0
    ).all()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "sessao": sessao,
        "contas": contas,
        "saldos": saldos,
        "total_saldos": total_saldos,
        "receitas": receitas_efetivadas,
        "despesas": despesas_efetivadas,
        "despesas_totais": despesas_totais,
        "despesas_pendentes": despesas_pendentes,
        "gastos_por_categoria": gastos_por_categoria,
        "faturas_abertas": faturas_abertas,
        "mes_atual": mes,
        "ano_atual": ano,
        "hoje": date.today()})


@router.get("/dashboard/chart-data")
async def get_chart_data(
    m: int = Query(...),
    y: int = Query(...),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
    ):
    from sqlalchemy.orm import aliased
    Pai = aliased(Categoria)
    
    gastos_cat_query = db.query(
        case(
            (Categoria.categorias_pai_id.isnot(None), func.concat(Pai.categorias_nome, " > ", Categoria.categorias_nome)),
            else_=Categoria.categorias_nome
        ).label("nome_completo"),
        func.sum(Operacao.operacoes_valor).label("total"),
        func.sum(case((Operacao.operacoes_efetivado == 1, Operacao.operacoes_valor), else_=0)).label("efetivado")
    ).join(Operacao, Operacao.operacoes_categoria == Categoria.categorias_id)\
     .outerjoin(Pai, Categoria.categorias_pai_id == Pai.categorias_id)\
     .filter(
        extract('month', Operacao.operacoes_data_lancamento) == m,
        extract('year', Operacao.operacoes_data_lancamento) == y,
        Operacao.operacoes_tipo == 3,
        Operacao.operacoes_validacao == 1,
    ).group_by(text("nome_completo")).all()

    gastos_por_categoria = [
        {"nome": g.nome_completo, "total": float(abs(g.total)), "efetivado": float(abs(g.efetivado))}
        for g in gastos_cat_query
    ]
    return {"gastos_por_categoria": gastos_por_categoria}
