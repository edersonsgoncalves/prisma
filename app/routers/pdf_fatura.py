# app/routers/pdf_fatura.py
"""
Router FastAPI para importação de faturas PDF de cartão de crédito.

Endpoints:
  GET  /importar-fatura                  → página de upload
  POST /importar-fatura/upload           → processa PDF, retorna JSON
  POST /importar-fatura/confirmar-completo → grava decisões no banco
"""

import os
import tempfile
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ContaBancaria
from app.auth import require_login
from app.templates import templates
from app.pdf_fatura_importer import FaturaImporter, FaturaRepository, LancamentoFatura

log = logging.getLogger("pdf_fatura_router")

router = APIRouter(prefix="/importar-fatura", tags=["pdf-fatura"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class LancamentoDecisao(BaseModel):
    hash_id:       str
    acao:          str             # "efetivar" | "inserir" | "ignorar"
    data:          str             # ISO date "YYYY-MM-DD"
    descricao:     str
    valor:         float
    tipo:          str             # "D" | "C"
    cartao_nome:   str
    cartao_final:  str
    parcela_atual: Optional[int] = None
    parcela_total: Optional[int] = None
    operacao_id:   Optional[int] = None
    adicional_id:  Optional[int] = None
    tipo_operacao: Optional[int] = None  # 1=Receita, 3=Despesa, 4=Transferência
    conta_destino: Optional[int] = None


class ConfirmarPayload(BaseModel):
    conta_id:    int
    adicionais:  list[dict] = []  # Lista para criar novos portadores
    lancamentos: list[LancamentoDecisao]


# ─── Serialização ─────────────────────────────────────────────────────────────

def _serializar(resultados) -> list[dict]:
    out = []
    for rc in resultados:
        l = rc.lancamento
        out.append({
            "hash_id":            l.hash_id,
            "data":               l.data.isoformat() if l.data else None,
            "descricao":          l.descricao,
            "valor":              float(l.valor),
            "tipo":               l.tipo,
            "cartao_nome":        l.cartao_nome,
            "cartao_final":       l.cartao_final,
            "parcela_atual":      l.parcela_atual,
            "parcela_total":      l.parcela_total,
            "score":              rc.score,
            "classificacao":      rc.classificacao,
            "operacao_id":        rc.operacao_id,
            "operacao_descricao": rc.operacao_descricao,
            "operacao_data":      rc.operacao_data.isoformat() if rc.operacao_data else None,
            "operacao_valor":     float(rc.operacao_valor) if rc.operacao_valor is not None else None,
        })
    return out


# ─── GET /importar-fatura ──────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def importar_fatura_page(
    request: Request,
    db: Session = Depends(get_db),
    sessao=Depends(require_login),
):
    cartoes = (
        db.query(ContaBancaria)
        .filter(ContaBancaria.tipo_conta == 4)
        .order_by(ContaBancaria.nome_conta)
        .all()
    )
    contas_todas = (
        db.query(ContaBancaria)
        .order_by(ContaBancaria.tipo_conta, ContaBancaria.nome_conta)
        .all()
    )
    return templates.TemplateResponse("importar_fatura.html", {
        "request":      request,
        "sessao":       sessao,
        "cartoes":      cartoes,
        "contas_todas": contas_todas,
    })


# ─── POST /importar-fatura/upload ─────────────────────────────────────────────

@router.post("/upload")
async def upload_fatura(
    arquivo:      UploadFile    = File(...),
    conta_id:     int           = Form(...),
    ano_override: Optional[int] = Form(None),
    db:           Session       = Depends(get_db),
    sessao=Depends(require_login),
):
    if not arquivo.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Apenas arquivos .pdf são aceitos.")

    conta = db.query(ContaBancaria).filter(ContaBancaria.conta_id == conta_id).first()
    if not conta:
        raise HTTPException(status_code=404, detail="Cartão não encontrado.")

    conteudo = await arquivo.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(conteudo)
        tmp_path = tmp.name

    try:
        importer   = FaturaImporter(db=db)
        resultados = importer.processar(tmp_path, conta_id, ano_override)
    except Exception as e:
        log.error(f"Erro ao processar PDF: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao processar PDF: {str(e)}")
    finally:
        os.unlink(tmp_path)

    return JSONResponse({
        "total":       len(resultados[0]),
        "lancamentos": _serializar(resultados[0]),
        "adicionais":  [vars(a) for a in resultados[1]],
    })


# ─── POST /importar-fatura/confirmar-completo ─────────────────────────────────

@router.post("/confirmar-completo")
async def confirmar_completo(
    payload: ConfirmarPayload,
    db:      Session = Depends(get_db),
    sessao=Depends(require_login),
):
    from datetime import date as _date
    from app.routers.lancamentos import get_or_create_fatura

    conta = db.query(ContaBancaria).filter(ContaBancaria.conta_id == payload.conta_id).first()
    if not conta:
        raise HTTPException(status_code=404, detail="Cartão não encontrado.")

    # 1. Primeiro garante a criação/atualização de portadores
    importer = FaturaImporter(db)
    repo = FaturaRepository(db)
    mapa_adicionais = {}
    if payload.adicionais:
        mapa_adicionais = importer.confirmar_adicionais(payload.conta_id, payload.adicionais)

    stats = {"efetivados": 0, "inseridos": 0, "ignorados": 0, "erros": 0}
    fatura_id_gerada = None

    # 2. Processa os lançamentos
    for item in payload.lancamentos:
        if item.acao == "ignorar":
            stats["ignorados"] += 1
            continue

        try:
            data_obj = _date.fromisoformat(item.data)

            # Se o adicional foi recém criado, atualiza o ID
            pid = item.adicional_id or mapa_adicionais.get(item.cartao_final)

            if item.acao == "efetivar":
                if not item.operacao_id:
                    log.warning(f"Efetivar sem operacao_id: {item.descricao} — inserindo como novo")
                    item.acao = "inserir"
                else:
                    repo.efetivar_operacao(item.operacao_id, data_obj, adicional_id=pid)
                    stats["efetivados"] += 1
                    continue

            if item.acao == "inserir":
                fatura_id = get_or_create_fatura(db, payload.conta_id, data_obj)
                if fatura_id_gerada is None:
                    fatura_id_gerada = fatura_id

                lanc = LancamentoFatura(
                    data=data_obj,
                    descricao=item.descricao,
                    valor=item.valor,
                    tipo=item.tipo,
                    cartao_nome=item.cartao_nome,
                    cartao_final=item.cartao_final,
                    parcela_atual=item.parcela_atual,
                    parcela_total=item.parcela_total,
                    hash_id=item.hash_id,
                    adicional_id=pid,
                    tipo_operacao=item.tipo_operacao or (1 if item.tipo == "C" else 3),
                    conta_destino=item.conta_destino
                )

                if lanc.tipo_operacao == 4 and lanc.conta_destino:
                    repo.inserir_transferencia(
                        lanc, payload.conta_id, lanc.conta_destino, fatura_id
                    )
                else:
                    repo.inserir_operacao(lanc, payload.conta_id, fatura_id)
                
                stats["inseridos"] += 1

        except Exception as e:
            log.error(f"Erro em '{item.descricao}': {e}")
            stats["erros"] += 1

    log.info(
        f"Importação concluída: {stats['efetivados']} efetivados | "
        f"{stats['inseridos']} inseridos | {stats['ignorados']} ignorados | "
        f"{stats['erros']} erros"
    )

    return JSONResponse({
        **stats,
        "fatura_id": fatura_id_gerada,
    })