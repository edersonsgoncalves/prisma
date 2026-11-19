"""Rotas do módulo de investimentos (Fase 1 - fundação e cadastros base)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from urllib.parse import quote

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
    InvestMovimentacao,
    InvestPosicao,
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
async def listar_instituicoes(request: Request, msg: Optional[str] = Query(None), erro: Optional[str] = Query(None), sessao=Depends(require_login), db: Session = Depends(get_db)):
    itens = db.query(InvestInstituicao).order_by(InvestInstituicao.nome).all()
    return templates.TemplateResponse("investimentos/instituicoes.html", {"request": request, "sessao": sessao, "itens": itens, "msg": msg, "erro": erro})


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
    try:
        if not nome.strip():
            raise ValueError("Informe o nome da instituição.")
        db.add(InvestInstituicao(nome=nome.strip(), cnpj=(cnpj or None), tipo=(tipo or None), observacoes=(observacoes or None), ativo=1))
        db.commit()
        return RedirectResponse(url=f"/investimentos/instituicoes?msg={quote('Instituição cadastrada com sucesso.')}", status_code=303)
    except Exception as e:
        db.rollback()
        return RedirectResponse(url=f"/investimentos/instituicoes?erro={quote(str(e))}", status_code=303)


# ── Carteiras ────────────────────────────────────────────────────────────────

@router.get("/carteiras", response_class=HTMLResponse)
async def listar_carteiras(request: Request, msg: Optional[str] = Query(None), erro: Optional[str] = Query(None), sessao=Depends(require_login), db: Session = Depends(get_db)):
    itens = db.query(InvestCarteira).order_by(InvestCarteira.nome).all()
    instituicoes = db.query(InvestInstituicao).filter(InvestInstituicao.ativo == 1).order_by(InvestInstituicao.nome).all()
    contas = db.query(ContaBancaria).order_by(ContaBancaria.nome_conta).all()
    moedas = service.listar_moedas(db)
    contas_map = {c.conta_id: c.nome_conta for c in contas}
    moedas_map = {m.idcontas_moedas: (m.contas_moedas_abrev or str(m.idcontas_moedas)) for m in moedas}
    return templates.TemplateResponse("investimentos/carteiras.html", {
        "request": request, "sessao": sessao, "itens": itens,
        "instituicoes": instituicoes, "contas": contas, "moedas": moedas,
        "contas_map": contas_map, "moedas_map": moedas_map,
        "msg": msg, "erro": erro,
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
    try:
        if not nome.strip():
            raise ValueError("Informe o nome da carteira.")
        db.add(InvestCarteira(
            nome=nome.strip(),
            instituicao_id=instituicao_id or None,
            conta_financeira_id=conta_financeira_id or None,
            moeda_base_id=moeda_base_id or None,
            descricao=(descricao or None),
            ativo=1,
        ))
        db.commit()
        return RedirectResponse(url=f"/investimentos/carteiras?msg={quote('Carteira cadastrada com sucesso.')}", status_code=303)
    except Exception as e:
        db.rollback()
        return RedirectResponse(url=f"/investimentos/carteiras?erro={quote(str(e))}", status_code=303)


# ── Ativos / Produtos ────────────────────────────────────────────────────────

@router.get("/ativos", response_class=HTMLResponse)
async def listar_ativos(request: Request, msg: Optional[str] = Query(None), erro: Optional[str] = Query(None), sessao=Depends(require_login), db: Session = Depends(get_db)):
    itens = db.query(InvestAtivo).order_by(InvestAtivo.nome).all()
    instituicoes = db.query(InvestInstituicao).filter(InvestInstituicao.ativo == 1).order_by(InvestInstituicao.nome).all()
    indexadores = db.query(InvestIndexador).order_by(InvestIndexador.nome).all()
    moedas = service.listar_moedas(db)
    moedas_map = {m.idcontas_moedas: (m.contas_moedas_abrev or str(m.idcontas_moedas)) for m in moedas}
    return templates.TemplateResponse("investimentos/ativos.html", {
        "request": request, "sessao": sessao, "itens": itens,
        "instituicoes": instituicoes, "indexadores": indexadores, "moedas": moedas,
        "moedas_map": moedas_map,
        "msg": msg, "erro": erro,
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
    try:
        if not nome.strip():
            raise ValueError("Informe o nome do ativo/produto.")
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
        return RedirectResponse(url=f"/investimentos/ativos?msg={quote('Ativo/produto cadastrado com sucesso.')}", status_code=303)
    except Exception as e:
        db.rollback()
        return RedirectResponse(url=f"/investimentos/ativos?erro={quote(str(e))}", status_code=303)


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


# ── Movimentações e posições (base Fase 2) ──────────────────────────────────

@router.get("/movimentacoes", response_class=HTMLResponse)
async def listar_movimentacoes(request: Request, msg: Optional[str] = Query(None), erro: Optional[str] = Query(None), sessao=Depends(require_login), db: Session = Depends(get_db)):
    itens = db.query(InvestMovimentacao).order_by(InvestMovimentacao.data_negociacao.desc(), InvestMovimentacao.id.desc()).limit(200).all()
    carteiras = db.query(InvestCarteira).filter(InvestCarteira.ativo == 1).order_by(InvestCarteira.nome).all()
    ativos = db.query(InvestAtivo).filter(InvestAtivo.ativo == 1).order_by(InvestAtivo.nome).all()
    instituicoes = db.query(InvestInstituicao).filter(InvestInstituicao.ativo == 1).order_by(InvestInstituicao.nome).all()
    moedas = service.listar_moedas(db)
    contas = db.query(ContaBancaria).order_by(ContaBancaria.nome_conta).all()
    return templates.TemplateResponse("investimentos/movimentacoes.html", {
        "request": request, "sessao": sessao, "itens": itens,
        "carteiras": carteiras, "ativos": ativos, "instituicoes": instituicoes, "moedas": moedas, "contas": contas,
        "msg": msg, "erro": erro,
    })


@router.post("/movimentacoes")
async def criar_movimentacao(
    request: Request,
    carteira_id: int = Form(...),
    tipo: str = Form(...),
    data_negociacao: str = Form(...),
    ativo_id: Optional[int] = Form(None),
    quantidade: Optional[str] = Form(None),
    preco_unitario: Optional[str] = Form(None),
    valor_bruto: Optional[str] = Form(None),
    taxas_custos: Optional[str] = Form(None),
    moeda_id: Optional[int] = Form(None),
    taxa_cambio: Optional[str] = Form(None),
    corretora_id: Optional[int] = Form(None),
    observacoes: Optional[str] = Form(None),
    integrar_financeiro: Optional[str] = Form(None),
    conta_externa_id: Optional[int] = Form(None),
    efetivado: Optional[int] = Form(1),
    sessao=Depends(require_login),
    db: Session = Depends(get_db),
):
    try:
        dt = date.fromisoformat(data_negociacao)
        qtd = _parse_decimal_br(quantidade, "0") if quantidade else None
        pu = _parse_decimal_br(preco_unitario, "0") if preco_unitario else None
        bruto = _parse_decimal_br(valor_bruto, "0") if valor_bruto else None
        custos = _parse_decimal_br(taxas_custos, "0") if taxas_custos else None
        cambio = _parse_decimal_br(taxa_cambio, "0") if taxa_cambio else None
        if cambio is not None and cambio == 0:
            cambio = None

        mov, integracao = service.registrar_movimentacao(
            db=db,
            carteira_id=carteira_id,
            tipo=tipo,
            data_negociacao=dt,
            ativo_id=ativo_id or None,
            quantidade=qtd,
            preco_unitario=pu,
            valor_bruto=bruto,
            taxas_custos=custos,
            moeda_id=moeda_id or None,
            taxa_cambio=cambio,
            corretora_id=corretora_id or None,
            observacoes=(observacoes or None),
            integrar_financeiro=bool(integrar_financeiro),
            conta_externa_id=conta_externa_id or None,
            efetivado=1 if efetivado is None else int(efetivado),
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(url=f"/investimentos/movimentacoes?erro={quote(str(e))}", status_code=303)

    msg = "Movimentação registrada"
    if integracao and integracao.get("ok"):
        msg += " e integrada ao financeiro (transferência criada)" if not integracao.get("reutilizado") else " (integração financeira já existente)"
    return RedirectResponse(url=f"/investimentos/movimentacoes?msg={quote(msg)}", status_code=303)


@router.get("/posicoes", response_class=HTMLResponse)
async def listar_posicoes(request: Request, sessao=Depends(require_login), db: Session = Depends(get_db)):
    itens = db.query(InvestPosicao).all()
    # ordena em python por carteira/ativo para simplicidade
    itens = sorted(itens, key=lambda p: (p.carteira_id, p.ativo_id))
    return templates.TemplateResponse("investimentos/posicoes.html", {"request": request, "sessao": sessao, "itens": itens})
