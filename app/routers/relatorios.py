"""routers/relatorios.py — Relatórios e totais por categoria."""
from pathlib import Path
from datetime import date
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, case, text

from app.database import get_db
from app.auth import require_login
from app.models import Operacao, Categoria

router = APIRouter(prefix="/relatorios", tags=["relatorios"])
from app.templates import templates


@router.get("/categorias", response_class=HTMLResponse)
async def totais_categoria(
    request: Request,
    m: int = Query(default=date.today().month),
    y: int = Query(default=date.today().year),
    sessao=Depends(require_login),
    db: Session = Depends(get_db),
):
    # Totais por categoria — 1 query com JOIN + GROUP BY
    # Usamos CASE para concatenar o nome do pai se existir
    from sqlalchemy.orm import aliased
    Pai = aliased(Categoria)
    
    totais_query = (
        db.query(
            case(
                (Categoria.categorias_pai_id.isnot(None), func.concat(Pai.categorias_nome, " > ", Categoria.categorias_nome)),
                else_=Categoria.categorias_nome
            ).label("nome_completo"),
            func.sum(Operacao.operacoes_valor).label("total")
        )
        .join(Operacao, Operacao.operacoes_categoria == Categoria.categorias_id)
        .outerjoin(Pai, Categoria.categorias_pai_id == Pai.categorias_id)
        .filter(
            extract('month', Operacao.operacoes_data_lancamento) == m,
            extract('year', Operacao.operacoes_data_lancamento) == y,
            Operacao.operacoes_validacao == 1,
        )
        .group_by(text("nome_completo"))
        .order_by(func.sum(Operacao.operacoes_valor))
        .all()
    )
    
    return templates.TemplateResponse("relatorios/totais_categoria.html", {
        "request": request, "sessao": sessao,
        "totais": totais_query, "mes": m, "ano": y,
    })
