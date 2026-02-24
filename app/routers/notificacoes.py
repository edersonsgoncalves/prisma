"""routers/notificacoes.py — Registro e visualização de logs/notificações."""
from datetime import datetime
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.auth import require_login
from app.models import LogOperacao
from app.templates import templates

router = APIRouter(prefix="/notificacoes", tags=["notificacoes"])

@router.get("", response_class=HTMLResponse)
async def listar_notificacoes(
    request: Request,
    sessao=Depends(require_login),
    db: Session = Depends(get_db),
):
    # Pega os últimos 50 logs
    logs = db.query(LogOperacao).order_by(desc(LogOperacao.log_timestamp)).limit(50).all()
    
    # Marca como lido (opcional)
    db.query(LogOperacao).filter(LogOperacao.log_lido == 0).update({"log_lido": 1})
    db.commit()
    
    return templates.TemplateResponse("notificacoes.html", {
        "request": request, "sessao": sessao, "logs": logs
    })
