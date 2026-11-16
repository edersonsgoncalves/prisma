# app/routers/ofx.py
from fastapi import APIRouter, Depends, File, UploadFile, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
import tempfile, os

from app.database import get_db
from app.models import ContaBancaria
from app.ofx_tools import analisar_ofx, efetivar_transacao, EfetivarPayload
from app.auth import require_login
from app.templates import templates

router = APIRouter(prefix="/ofx", tags=["ofx"])


# ---------------------------------------------------------------------------
# GET /ofx/importar  — página de importação
# ---------------------------------------------------------------------------
@router.get("/importar", response_class=HTMLResponse)
async def importar_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_login),
):
    contas = db.query(ContaBancaria).all()
    return templates.TemplateResponse(
        request,
        "ofx_import.html",
        {"contas": contas, "current_user": current_user, "sessao": current_user},
    )


# ---------------------------------------------------------------------------
# POST /ofx/analisar  — recebe arquivo, devolve JSON com matches
# ---------------------------------------------------------------------------
@router.post("/analisar")
async def analisar(
    arquivo: UploadFile = File(...),
    conta_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_login),
):
    if not arquivo.filename.lower().endswith(".ofx"):
        raise HTTPException(status_code=400, detail="Apenas arquivos .ofx são aceitos.")

    if conta_id:
        conta = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta_id).first()
        if not conta:
            raise HTTPException(status_code=404, detail="Conta não encontrada.")

    conteudo = await arquivo.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ofx") as tmp:
        tmp.write(conteudo)
        tmp_path = tmp.name

    try:
        resultado = analisar_ofx(
            filepath=tmp_path,
            db=db,
            conta_id=conta_id,
        )
    finally:
        os.unlink(tmp_path)

    return JSONResponse(content=resultado)


# ---------------------------------------------------------------------------
# POST /ofx/efetivar  — aplica uma transação (efetivar | inserir | forcar)
# ---------------------------------------------------------------------------
@router.post("/efetivar")
async def efetivar(
    payload: EfetivarPayload,
    db: Session = Depends(get_db),
    current_user=Depends(require_login),
):
    try:
        resultado = efetivar_transacao(payload=payload, db=db)
        return JSONResponse(content=resultado)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")