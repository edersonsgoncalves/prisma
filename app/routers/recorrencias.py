"""routers/recorrencias.py"""
from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import require_login
from app.models import Recorrencia

router = APIRouter(prefix="/recorrencias", tags=["recorrencias"])
from app.templates import templates

@router.get("", response_class=HTMLResponse)
async def listar(request: Request, sessao=Depends(require_login), db=Depends(get_db)):
    recs = db.query(Recorrencia).all()
    return templates.TemplateResponse("recorrencias.html",
        {"request": request, "sessao": sessao, "recorrencias": recs})

@router.post("/nova")
async def nova(frequencia: str = Form(...), dias_uteis: int = Form(default=0),
               db=Depends(get_db), sessao=Depends(require_login)):
    db.add(Recorrencia(frequencia=frequencia, dias_uteis=dias_uteis))
    db.commit()
    return RedirectResponse(url="/recorrencias", status_code=302)
