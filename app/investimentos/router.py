"""Rotas do módulo de investimentos (Fase 1 - fundação e cadastros base)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import require_login
from app.database import get_db
from app.templates import templates
from app.models import ContaBancaria

from .models import (
    InvestAtivo,
    InvestCarteira,
    InvestIndexador,
    InvestInstituicao,
    InvestTaxaCambio,
)
from . import service

router = APIRouter(prefix="/investimentos", tags=["investimentos"])


def _parse_decimal_br(value: Optional[str], default: str = "0") -> Decimal:
    if value is None or str(value).strip() == "":
        return Decimal(default)
    s = str(value).strip().replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except Exception:
        return Decimal(default)


@router.get("", response_class=HTMLResponse)
async def investimentos_home(
    request: Request,
    sessao=Depends(require_login),
    db: Session = Depends(get_db),
):
    carteiras = db.query(InvestCarteira).order_by(InvestCarteira.nome).all()
    ativos = db.query(InvestAtivo).order_by(InvestAtivo.nome).all()
    instituicoes = db.query(InvestInstituicao).order_by(InvestInstituicao.nome).all()
    return templates.TemplateResponse("investimentos/index.html", {
        "request": request,
        "sessao": sessao,
        "carteiras": carteiras,
        "ativos": ativos,
        "instituicoes": instituicoes,
    })


# ── Instituições / Corretoras ────────────────────────────────────────────────

@router.get("/instituicoes", response_class=HTMLResponse)
async def listar_instituicoes(request: Request, sessao=Depends(require_login), db: Session = Depends(get_db)):
    itens = db.query(InvestInstituicao).order_by(InvestInstituicao.nome).all()
    return templates.TemplateResponse("investimentos/instituicoes.html", {"request": request, "sessao": sessao, "itens": itens})


@router.post("/instituicoes")
async def criar_instituicao(
    request: Request,
    nome: str = Form(...),
    cnpj: Optional[str] = Form(None),
    tipo: Optional[str] = Form(None),
    observacoes: Optional[str] = Form(None),
    sessao=Depends(require_login),
    db: Session = Depends(get_db),
):
    db.add(InvestInstituicao(nome=nome.strip(), cnpj=(cnpj or None), tipo=(tipo or None), observacoes=(observacoes or None), ativo=1))
    db.commit()
    return RedirectResponse(url="/investimentos/instituicoes", status_code=303)


# ── Carteiras ────────────────────────────────────────────────────────────────

@router.get("/carteiras", response_class=HTMLResponse)
async def listar_carteiras(request: Request, sessao=Depends(require_login), db: Session = Depends(get_db)):
    itens = db.query(InvestCarteira).order_by(InvestCarteira.nome).all()
    instituicoes = db.query(InvestInstituicao).filter(InvestInstituicao.ativo == 1).order_by(InvestInstituicao.nome).all()
    contas = db.query(ContaBancaria).order_by(ContaBancaria.nome_conta).all()
    moedas = service.listar_moedas(db)
    return templates.TemplateResponse("investimentos/carteiras.html", {
        "request": request, "sessao": sessao, "itens": itens,
        "instituicoes": instituicoes, "contas": contas, "moedas": moedas,
    })


@router.post("/carteiras")
async def criar_carteira(
    request: Request,
    nome: str = Form(...),
    instituicao_id: Optional[int] = Form(None),
    conta_financeira_id: Optional[int] = Form(None),
    moeda_base_id: Optional[int] = Form(None),
    descricao: Optional[str] = Form(None),
    sessao=Depends(require_login),
    db: Session = Depends(get_db),
):
    db.add(InvestCarteira(
        nome=nome.strip(),
        instituicao_id=instituicao_id or None,
        conta_financeira_id=conta_financeira_id or None,
        moeda_base_id=moeda_base_id or None,
        descricao=(descricao or None),
        ativo=1,
    ))
    db.commit()
    return RedirectResponse(url="/investimentos/carteiras", status_code=303)


# ── Ativos / Produtos ────────────────────────────────────────────────────────

@router.get("/ativos", response_class=HTMLResponse)
async def listar_ativos(request: Request, sessao=Depends(require_login), db: Session = Depends(get_db)):
    itens = db.query(InvestAtivo).order_by(InvestAtivo.nome).all()
    instituicoes = db.query(InvestInstituicao).filter(InvestInstituicao.ativo == 1).order_by(InvestInstituicao.nome).all()
    indexadores = db.query(InvestIndexador).order_by(InvestIndexador.nome).all()
    moedas = service.listar_moedas(db)
    return templates.TemplateResponse("investimentos/ativos.html", {
        "request": request, "sessao": sessao, "itens": itens,
        "instituicoes": instituicoes, "indexadores": indexadores, "moedas": moedas,
    })


@router.post("/ativos")
async def criar_ativo(
    request: Request,
    nome: str = Form(...),
    classe: str = Form(...),
    tipo_produto: Optional[str] = Form(None),
    ticker: Optional[str] = Form(None),
    isin: Optional[str] = Form(None),
    emissor: Optional[str] = Form(None),
    instituicao_id: Optional[int] = Form(None),
    moeda_id: Optional[int] = Form(None),
    indexador_id: Optional[int] = Form(None),
    taxa_contratada: Optional[str] = Form(None),
    data_vencimento: Optional[str] = Form(None),
    liquidez_diaria: Optional[int] = Form(0),
    observacoes: Optional[str] = Form(None),
    sessao=Depends(require_login),
    db: Session = Depends(get_db),
):
    dv = date.fromisoformat(data_vencimento) if data_vencimento else None
    taxa = _parse_decimal_br(taxa_contratada, "0") if taxa_contratada else None
    db.add(InvestAtivo(
        nome=nome.strip(),
        classe=classe.strip(),
        tipo_produto=(tipo_produto or None),
        ticker=(ticker.strip() if ticker else None),
        isin=(isin.strip() if isin else None),
        emissor=(emissor or None),
        instituicao_id=instituicao_id or None,
        moeda_id=moeda_id or None,
        indexador_id=indexador_id or None,
        taxa_contratada=taxa,
        data_vencimento=dv,
        liquidez_diaria=1 if liquidez_diaria else 0,
        observacoes=(observacoes or None),
        ativo=1,
    ))
    db.commit()
    return RedirectResponse(url="/investimentos/ativos", status_code=303)


# ── Câmbio (histórico e atualização via BrasilAPI) ────────────────────────────

@router.get("/cambio", response_class=HTMLResponse)
async def cambio_view(
    request: Request,
    msg: Optional[str] = Query(None),
    erro: Optional[str] = Query(None),
    sessao=Depends(require_login),
    db: Session = Depends(get_db),
):
    moedas = service.listar_moedas(db)
    taxas = db.query(InvestTaxaCambio).order_by(InvestTaxaCambio.data_referencia.desc(), InvestTaxaCambio.id.desc()).limit(100).all()
    return templates.TemplateResponse("investimentos/cambio.html", {
        "request": request, "sessao": sessao, "moedas": moedas, "taxas": taxas, "msg": msg, "erro": erro,
    })


@router.post("/cambio/atualizar")
async def cambio_atualizar(
    request: Request,
    moeda_abrev: str = Form(...),
    data_referencia: str = Form(...),
    sessao=Depends(require_login),
    db: Session = Depends(get_db),
):
    try:
        dt = date.fromisoformat(data_referencia)
    except Exception:
        return RedirectResponse(url="/investimentos/cambio?erro=Data+inválida", status_code=303)

    res = service.atualizar_cambio_brasilapi(db, moeda_abrev=moeda_abrev, data_referencia=dt, moeda_destino_abrev="BRL")
    if res.get("ok"):
        return RedirectResponse(url=f"/investimentos/cambio?msg=Cotação+atualizada:+{moeda_abrev}/BRL+={res.get('taxa')}+({res.get('data_referencia')})", status_code=303)
    erro = res.get("erro", "Erro ao atualizar cotação")
    return RedirectResponse(url=f"/investimentos/cambio?erro={erro}", status_code=303)


@router.post("/cambio/manual")
async def cambio_manual(
    request: Request,
    moeda_origem_id: int = Form(...),
    moeda_destino_id: int = Form(...),
    data_referencia: str = Form(...),
    taxa: str = Form(...),
    sessao=Depends(require_login),
    db: Session = Depends(get_db),
):
    try:
        dt = date.fromisoformat(data_referencia)
        valor = _parse_decimal_br(taxa, "0")
        if valor <= 0:
            raise ValueError("taxa inválida")
    except Exception:
        return RedirectResponse(url="/investimentos/cambio?erro=Dados+inválidos+para+taxa+manual", status_code=303)

    service.registrar_taxa_cambio(db, moeda_origem_id, moeda_destino_id, dt, valor, tipo="MANUAL", fonte="MANUAL")
    return RedirectResponse(url="/investimentos/cambio?msg=Taxa+manual+registrada", status_code=303)
