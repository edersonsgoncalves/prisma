"""routers/categorias.py"""
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import require_login
from app.models import Categoria

router = APIRouter(prefix="/categorias", tags=["categorias"])
from app.templates import templates

@router.get("", response_class=HTMLResponse)
async def listar(request: Request, sessao=Depends(require_login), db=Depends(get_db)):
    cats = db.query(Categoria).order_by(Categoria.categorias_nome).all()
    return templates.TemplateResponse("categorias.html",
        {"request": request, "sessao": sessao, "categorias": cats})

@router.post("/nova")
async def nova(nome: str = Form(...), classe: int = Form(default=0),
               pai_id: Optional[int] = Form(default=None),
               db=Depends(get_db), sessao=Depends(require_login)):
    db.add(Categoria(categorias_nome=nome, categorias_classe=classe, categorias_pai_id=pai_id))
    db.commit()
    return RedirectResponse(url="/categorias", status_code=302)

@router.post("/deletar/{cat_id}")
async def deletar(cat_id: int, db=Depends(get_db), sessao=Depends(require_login)):
    cat = db.query(Categoria).filter(Categoria.categorias_id == cat_id).first()
    if cat:
        db.delete(cat); db.commit()
    return RedirectResponse(url="/categorias", status_code=302)
