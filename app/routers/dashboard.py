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
    # Contas bancárias (exceto cartões tipo 4)
    contas = db.query(ContaBancaria).filter(ContaBancaria.tipo_conta != 4).all()

    # Saldo de cada conta no período e saldo total
    saldos = {}
    total_saldos = 0
    for conta in contas:
        total = db.query(func.sum(Operacao.operacoes_valor)).filter(
            Operacao.operacoes_conta == conta.conta_id,
            Operacao.operacoes_efetivado == 1,
            Operacao.operacoes_validacao == 1,
        ).scalar() or 0
        saldos[conta.conta_id] = total
        total_saldos += total

    # Receitas do mês (confirmadas/efetivadas)
    receitas_efetivadas = db.query(func.sum(Operacao.operacoes_valor)).filter(
        extract('month', Operacao.operacoes_data_lancamento) == mes,
        extract('year', Operacao.operacoes_data_lancamento) == ano,
        Operacao.operacoes_tipo == 1,
        Operacao.operacoes_efetivado == 1,
        Operacao.operacoes_validacao == 1,
    ).scalar() or 0

    # Despesas do mês (totais previstas e efetivadas separadamente)
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
