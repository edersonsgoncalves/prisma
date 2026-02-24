"""routers/projetos.py"""
from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
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

@router.post("/novo")
async def novo(nome: str = Form(...), inicio: str = Form(default=None),
               fim: str = Form(default=None), cor: str = Form(default="#6366f1"),
               db=Depends(get_db), sessao=Depends(require_login)):
    from datetime import date
    p = Projeto(projetos_nome=nome, projetos_cor=cor,
                projetos_inicio=date.fromisoformat(inicio) if inicio else None,
                projetos_fim=date.fromisoformat(fim) if fim else None)
    db.add(p); db.commit()
    return RedirectResponse(url="/projetos", status_code=302)
