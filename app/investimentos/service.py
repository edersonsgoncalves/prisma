"""Serviços do módulo de investimentos (cadastros, câmbio, integração)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models import ContasMoeda
from . import brasilapi
from .models import InvestCarteira, InvestInstituicao, InvestTaxaCambio


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
