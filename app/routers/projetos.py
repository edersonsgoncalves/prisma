"""routers/projetos.py"""
from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import require_login
from app.models import Projeto

router = APIRouter(prefix="/projetos", tags=["projetos"])
from app.templates import templates

@router.get("", response_class=HTMLResponse)
async def listar(request: Request, sessao=Depends(require_login), db=Depends(get_db)):
    projetos = db.query(Projeto).order_by(Projeto.projetos_nome).all()
    return templates.TemplateResponse("projetos.html",
        {"request": request, "sessao": sessao, "projetos": projetos})

def _criar_projeto(nome: str, inicio, fim, cor, db: Session):
    from datetime import date
    p = Projeto(
        projetos_nome=nome.strip(),
        projetos_cor=(cor or "#6366f1").strip() or "#6366f1",
        projetos_inicio=date.fromisoformat(inicio) if inicio else None,
        projetos_fim=date.fromisoformat(fim) if fim else None,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.post("/novo")
async def novo(nome: str = Form(...), inicio: str = Form(default=None),
               fim: str = Form(default=None), cor: str = Form(default="#6366f1"),
               db=Depends(get_db), sessao=Depends(require_login)):
    _criar_projeto(nome, inicio, fim, cor, db)
    return RedirectResponse(url="/projetos", status_code=302)


@router.post("/api/novo")
async def novo_api(nome: str = Form(...), inicio: str = Form(default=None),
                   fim: str = Form(default=None), cor: str = Form(default="#6366f1"),
                   db=Depends(get_db), sessao=Depends(require_login)):
    nome = (nome or "").strip()
    if not nome:
        return JSONResponse(status_code=400, content={"erro": "Nome do projeto é obrigatório."})
    p = _criar_projeto(nome, inicio, fim, cor, db)
    return JSONResponse(content={
        "id": p.projeto_id,
        "nome": p.projetos_nome,
        "cor": p.projetos_cor,
    })
