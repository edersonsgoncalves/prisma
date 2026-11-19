"""Serviços do módulo de investimentos (cadastros, câmbio, integração)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models import ContasMoeda, Operacao
from . import brasilapi
from .models import (
    InvestAtivo,
    InvestCarteira,
    InvestInstituicao,
    InvestLote,
    InvestMovimentacao,
    InvestPosicao,
    InvestTaxaCambio,
)


# ── Moedas (reuso de contas_moedas) ──────────────────────────────────────────

def listar_moedas(db: Session):
    return db.query(ContasMoeda).order_by(ContasMoeda.idcontas_moedas).all()


def obter_moeda(db: Session, moeda_id: int) -> Optional[ContasMoeda]:
    return db.query(ContasMoeda).filter(ContasMoeda.idcontas_moedas == moeda_id).first()


def obter_moeda_por_abrev(db: Session, abrev: str) -> Optional[ContasMoeda]:
    if not abrev:
        return None
    return db.query(ContasMoeda).filter(ContasMoeda.contas_moedas_abrev == abrev.upper()).first()


# ── Câmbio (histórico em invest_taxas_cambio) ────────────────────────────────

def registrar_taxa_cambio(
    db: Session,
    moeda_origem_id: int,
    moeda_destino_id: int,
    data_referencia: date,
    taxa: Decimal,
    tipo: str = "FECHAMENTO",
    fonte: Optional[str] = None,
) -> InvestTaxaCambio:
    existente = db.query(InvestTaxaCambio).filter(
        InvestTaxaCambio.moeda_origem_id == moeda_origem_id,
        InvestTaxaCambio.moeda_destino_id == moeda_destino_id,
        InvestTaxaCambio.data_referencia == data_referencia,
        InvestTaxaCambio.tipo == tipo,
    ).first()
    if existente:
        existente.taxa = taxa
        existente.fonte = fonte or existente.fonte
        db.add(existente)
        db.commit()
        db.refresh(existente)
        return existente

    reg = InvestTaxaCambio(
        moeda_origem_id=moeda_origem_id,
        moeda_destino_id=moeda_destino_id,
        data_referencia=data_referencia,
        taxa=taxa,
        tipo=tipo,
        fonte=fonte,
        criado_em=datetime.utcnow(),
    )
    db.add(reg)
    db.commit()
    db.refresh(reg)
    return reg


def obter_taxa_cambio(db: Session, moeda_origem_id: int, moeda_destino_id: int, data_referencia: date, tipo: str = "FECHAMENTO") -> Optional[InvestTaxaCambio]:
    # taxa exata
    reg = db.query(InvestTaxaCambio).filter(
        InvestTaxaCambio.moeda_origem_id == moeda_origem_id,
        InvestTaxaCambio.moeda_destino_id == moeda_destino_id,
        InvestTaxaCambio.data_referencia == data_referencia,
        InvestTaxaCambio.tipo == tipo,
    ).first()
    if reg:
        return reg
    # fallback: última disponível anterior (qualquer tipo, preferindo mesma data/tipo já coberto acima)
    return db.query(InvestTaxaCambio).filter(
        InvestTaxaCambio.moeda_origem_id == moeda_origem_id,
        InvestTaxaCambio.moeda_destino_id == moeda_destino_id,
        InvestTaxaCambio.data_referencia <= data_referencia,
    ).order_by(InvestTaxaCambio.data_referencia.desc(), InvestTaxaCambio.id.desc()).first()


def atualizar_cambio_brasilapi(db: Session, moeda_abrev: str, data_referencia: date, moeda_destino_abrev: str = "BRL") -> dict:
    """Busca cotação na BrasilAPI e registra em invest_taxas_cambio (reuso de contas_moedas)."""
    moeda_origem = obter_moeda_por_abrev(db, moeda_abrev)
    moeda_destino = obter_moeda_por_abrev(db, moeda_destino_abrev)
    if not moeda_origem or not moeda_destino:
        return {"ok": False, "erro": f"Moeda não cadastrada em contas_moedas: {moeda_abrev} ou {moeda_destino_abrev}"}

    try:
        dados = brasilapi.obter_cotacao_cambio(moeda_abrev, data_referencia)
    except Exception as e:
        return {"ok": False, "erro": f"Falha ao consultar BrasilAPI: {e}"}

    taxa = dados.get("fechamento") or dados.get("taxa_venda") or dados.get("taxa_compra")
    if taxa is None:
        return {"ok": False, "erro": "Cotação não retornada pela BrasilAPI", "dados": dados}

    reg = registrar_taxa_cambio(
        db,
        moeda_origem_id=moeda_origem.idcontas_moedas,
        moeda_destino_id=moeda_destino.idcontas_moedas,
        data_referencia=dados.get("data_referencia") or data_referencia,
        taxa=Decimal(str(taxa)),
        tipo="FECHAMENTO",
        fonte="BRASILAPI",
    )
    return {"ok": True, "taxa": str(reg.taxa), "data_referencia": reg.data_referencia.isoformat(), "id": reg.id, "dados": dados}


# ── Integração financeira (esqueleto/contrato) ───────────────────────────────

class IntegracaoFinanceiraService:
    """Contrato interno para integração entre investimentos e finanças pessoais.

    Princípios (Fase 0): aportes/resgates como transferências de caixa; módulo de
    investimentos é fonte de verdade de posições/P&L; operações idempotentes e rastreáveis.
    Implementação concreta dos lançamentos financeiros deve respeitar convenções do
    domínio financeiro (tipo/contas/referências) e será evoluída na Fase 1/4.
    """

    @staticmethod
    def registrar_aporte_transferencia(db: Session, carteira: InvestCarteira, conta_origem_id: int, valor: Decimal, moeda_id: Optional[int], data_mov: date, taxa_cambio: Optional[Decimal] = None, id_correlacao: Optional[str] = None) -> dict:
        """Esqueleto: valida vínculo e retorna intenção de transferência (aporte)."""
        if not carteira.conta_financeira_id:
            return {"ok": False, "erro": "Carteira sem conta financeira vinculada."}
        # Fase 1: criar operação financeira (transferência) de forma idempotente e vincular
        return {
            "ok": True,
            "acao": "APORTE_TRANSFERENCIA",
            "conta_origem_id": conta_origem_id,
            "conta_destino_id": carteira.conta_financeira_id,
            "valor": str(valor),
            "moeda_id": moeda_id,
            "data": data_mov.isoformat(),
            "taxa_cambio": str(taxa_cambio) if taxa_cambio is not None else None,
            "id_correlacao": id_correlacao,
            "implementado": False,
        }

    @staticmethod
    def registrar_resgate_transferencia(db: Session, carteira: InvestCarteira, conta_destino_id: int, valor_liquido: Decimal, moeda_id: Optional[int], data_mov: date, taxa_cambio: Optional[Decimal] = None, id_correlacao: Optional[str] = None) -> dict:
        """Esqueleto: valida vínculo e retorna intenção de transferência (resgate pelo valor líquido)."""
        if not carteira.conta_financeira_id:
            return {"ok": False, "erro": "Carteira sem conta financeira vinculada."}
        # Fase 1: criar operação financeira (transferência) de forma idempotente e vincular
        return {
            "ok": True,
            "acao": "RESGATE_TRANSFERENCIA",
            "conta_origem_id": carteira.conta_financeira_id,
            "conta_destino_id": conta_destino_id,
            "valor_liquido": str(valor_liquido),
            "moeda_id": moeda_id,
            "data": data_mov.isoformat(),
            "taxa_cambio": str(taxa_cambio) if taxa_cambio is not None else None,
            "id_correlacao": id_correlacao,
            "implementado": False,
        }


# ── Posições e movimentações (fundação) ──────────────────────────────────────

def obter_ou_criar_posicao(db: Session, carteira_id: int, ativo_id: int) -> InvestPosicao:
    pos = db.query(InvestPosicao).filter(
        InvestPosicao.carteira_id == carteira_id,
        InvestPosicao.ativo_id == ativo_id,
    ).first()
    if pos:
        return pos
    pos = InvestPosicao(carteira_id=carteira_id, ativo_id=ativo_id, quantidade=Decimal("0"), preco_medio=Decimal("0"), custo_total=Decimal("0"), custo_total_brl=Decimal("0"))
    db.add(pos)
    db.flush()
    return pos


def _to_decimal(val, default="0") -> Decimal:
    if val is None or val == "":
        return Decimal(default)
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal(default)


def registrar_movimentacao(
    db: Session,
    carteira_id: int,
    tipo: str,
    data_negociacao: date,
    ativo_id: Optional[int] = None,
    quantidade: Optional[Decimal] = None,
    preco_unitario: Optional[Decimal] = None,
    valor_bruto: Optional[Decimal] = None,
    taxas_custos: Optional[Decimal] = None,
    moeda_id: Optional[int] = None,
    taxa_cambio: Optional[Decimal] = None,
    corretora_id: Optional[int] = None,
    observacoes: Optional[str] = None,
    id_correlacao: Optional[str] = None,
) -> InvestMovimentacao:
    """Registra movimentação e atualiza posição/lotes de forma simplificada (Fase 1/2 base)."""
    qtd = _to_decimal(quantidade)
    pu = _to_decimal(preco_unitario)
    bruto = _to_decimal(valor_bruto)
    custos = _to_decimal(taxas_custos)
    if bruto == 0 and qtd != 0 and pu != 0:
        bruto = (qtd * pu).quantize(Decimal("0.01"))
    liquido = (bruto - custos) if tipo in ("VENDA", "RESGATE") else (bruto + custos)
    if liquido == 0:
        liquido = bruto
    valor_brl = None
    if taxa_cambio is not None:
        try:
            valor_brl = (liquido * Decimal(str(taxa_cambio))).quantize(Decimal("0.01"))
        except Exception:
            valor_brl = None

    mov = InvestMovimentacao(
        carteira_id=carteira_id,
        ativo_id=ativo_id,
        tipo=tipo.upper(),
        data_negociacao=data_negociacao,
        data_liquidacao=data_negociacao,
        quantidade=qtd if qtd != 0 else None,
        preco_unitario=pu if pu != 0 else None,
        valor_bruto=bruto,
        taxas_custos=custos,
        valor_liquido=liquido,
        moeda_id=moeda_id,
        taxa_cambio=taxa_cambio,
        valor_brl=valor_brl,
        corretora_id=corretora_id,
        observacoes=observacoes,
        id_correlacao=id_correlacao,
    )
    db.add(mov)
    db.flush()

    # Atualização de posição/lotes para operações com ativo e quantidade
    if ativo_id and qtd != 0 and tipo.upper() in ("COMPRA", "APLICACAO", "APORTE_ATIVO", "VENDA", "RESGATE_ATIVO"):
        pos = obter_ou_criar_posicao(db, carteira_id, ativo_id)
        if tipo.upper() in ("COMPRA", "APLICACAO", "APORTE_ATIVO"):
            # entrada: novo lote e recálculo de preço médio
            novo_custo = liquido if liquido != 0 else bruto
            novo_custo_brl = valor_brl if valor_brl is not None else Decimal("0")
            pos.quantidade = (Decimal(str(pos.quantidade or 0)) + qtd)
            pos.custo_total = (Decimal(str(pos.custo_total or 0)) + novo_custo)
            pos.custo_total_brl = (Decimal(str(pos.custo_total_brl or 0)) + novo_custo_brl)
            if pos.quantidade != 0:
                pos.preco_medio = (pos.custo_total / pos.quantidade)
            lote = InvestLote(
                posicao_id=pos.id,
                movimento_id=mov.id,
                data_aquisicao=data_negociacao,
                quantidade=qtd,
                quantidade_aberta=qtd,
                preco_unitario=pu if pu != 0 else (novo_custo / qtd if qtd else Decimal("0")),
                custo_total=novo_custo,
                taxa_cambio=taxa_cambio,
                custo_total_brl=novo_custo_brl,
                corretora_id=corretora_id,
            )
            db.add(lote)
        elif tipo.upper() in ("VENDA", "RESGATE_ATIVO"):
            # saída simplificada (baixa proporcional/FIFO básica no agregado)
            pos.quantidade = (Decimal(str(pos.quantidade or 0)) - qtd)
            custo_saida = (Decimal(str(pos.preco_medio or 0)) * qtd)
            pos.custo_total = (Decimal(str(pos.custo_total or 0)) - custo_saida)
            if pos.quantidade <= 0:
                pos.quantidade = Decimal("0")
                pos.custo_total = Decimal("0")
                pos.preco_medio = Decimal("0")
            # baixa em lotes abertos (FIFO)
            restante = qtd
            lotes = db.query(InvestLote).filter(InvestLote.posicao_id == pos.id, InvestLote.quantidade_aberta > 0).order_by(InvestLote.data_aquisicao.asc(), InvestLote.id.asc()).all()
            for l in lotes:
                if restante <= 0:
                    break
                ab = Decimal(str(l.quantidade_aberta))
                baixa = ab if ab <= restante else restante
                l.quantidade_aberta = ab - baixa
                restante -= baixa
        db.add(pos)

    db.commit()
    db.refresh(mov)
    return mov
