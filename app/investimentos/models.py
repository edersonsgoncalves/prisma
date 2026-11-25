"""
Modelos do módulo de investimentos (tabelas invest_*).

Domínio isolado: sem FKs diretas para tabelas financeiras do Prisma.
Referências a entidades externas (ex.: conta financeira, moeda em contas_moedas)
são armazenadas por identificador e validadas por serviços/contratos.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String, Text, UniqueConstraint, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ──────────────────────────────────────────────
# Instituições / Corretoras
# ──────────────────────────────────────────────
class InvestInstituicao(Base):
    __tablename__ = "invest_instituicoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(150))
    cnpj: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tipo: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # corretora, banco, custodiante
    observacoes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ativo: Mapped[int] = mapped_column(SmallInteger, default=1)

    carteiras: Mapped[list["InvestCarteira"]] = relationship(back_populates="instituicao")


# ──────────────────────────────────────────────
# Carteiras / Contas de investimento
# ──────────────────────────────────────────────
class InvestCarteira(Base):
    __tablename__ = "invest_carteiras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(150))
    instituicao_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("invest_instituicoes.id"), nullable=True)
    # referência à conta financeira (contas_bancarias.conta_id), sem FK direta
    conta_financeira_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    moeda_base_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # ref contas_moedas (BRL padrão)
    descricao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ativo: Mapped[int] = mapped_column(SmallInteger, default=1)

    instituicao: Mapped[Optional[InvestInstituicao]] = relationship(back_populates="carteiras")
    posicoes: Mapped[list["InvestPosicao"]] = relationship(back_populates="carteira")


# ──────────────────────────────────────────────
# Indexadores
# ──────────────────────────────────────────────
class InvestIndexador(Base):
    __tablename__ = "invest_indexadores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(30), unique=True)  # CDI, SELIC, IPCA, PREFIXADO
    nome: Mapped[str] = mapped_column(String(100))
    descricao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# ──────────────────────────────────────────────
# Ativos / Produtos
# ──────────────────────────────────────────────
class InvestAtivo(Base):
    __tablename__ = "invest_ativos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # classe: RENDA_FIXA, CAIXINHA, ACAO, ETF, FI, FII, OUTRO
    classe: Mapped[str] = mapped_column(String(30))
    # tipo/produto: CDB, LCI, LCA, DEBENTURE, TESOURO, ACAO, ETF, FI, FII etc.
    tipo_produto: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    nome: Mapped[str] = mapped_column(String(200))
    ticker: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    isin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    emissor: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    instituicao_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("invest_instituicoes.id"), nullable=True)
    moeda_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # ref contas_moedas
    indexador_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("invest_indexadores.id"), nullable=True)
    taxa_contratada: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8), nullable=True)  # % CDI, taxa fixa etc.
    data_emissao: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    data_vencimento: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    liquidez_diaria: Mapped[int] = mapped_column(SmallInteger, default=0)
    observacoes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ativo: Mapped[int] = mapped_column(SmallInteger, default=1)

    indexador: Mapped[Optional[InvestIndexador]] = relationship()
    instituicao: Mapped[Optional[InvestInstituicao]] = relationship()

    __table_args__ = (Index("ix_invest_ativos_ticker", "ticker"),)


# ──────────────────────────────────────────────
# Taxas de câmbio (histórico)
# ──────────────────────────────────────────────
class InvestTaxaCambio(Base):
    __tablename__ = "invest_taxas_cambio"
    __table_args__ = (
        UniqueConstraint("moeda_origem_id", "moeda_destino_id", "data_referencia", "tipo", name="uq_invest_cambio"),
        Index("ix_invest_cambio_data", "data_referencia"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    moeda_origem_id: Mapped[int] = mapped_column(Integer)  # ref contas_moedas
    moeda_destino_id: Mapped[int] = mapped_column(Integer)  # ref contas_moedas (ex.: BRL)
    data_referencia: Mapped[date] = mapped_column(Date)
    taxa: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    # tipo/fonte: FECHAMENTO, PTAX, NEGOCIACAO, MANUAL, BRASILAPI
    tipo: Mapped[str] = mapped_column(String(30), default="FECHAMENTO")
    fonte: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ──────────────────────────────────────────────
# Cotações / Preços de ativos
# ──────────────────────────────────────────────
class InvestCotacao(Base):
    __tablename__ = "invest_cotacoes"
    __table_args__ = (
        UniqueConstraint("ativo_id", "data_referencia", name="uq_invest_cotacao"),
        Index("ix_invest_cotacao_data", "data_referencia"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ativo_id: Mapped[int] = mapped_column(Integer, ForeignKey("invest_ativos.id"))
    data_referencia: Mapped[date] = mapped_column(Date)
    preco: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    moeda_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # ref contas_moedas
    fonte: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    ativo: Mapped[InvestAtivo] = relationship()


# ──────────────────────────────────────────────
# Posições (consolidado por carteira/ativo)
# ──────────────────────────────────────────────
class InvestPosicao(Base):
    __tablename__ = "invest_posicoes"
    __table_args__ = (UniqueConstraint("carteira_id", "ativo_id", name="uq_invest_posicao"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    carteira_id: Mapped[int] = mapped_column(Integer, ForeignKey("invest_carteiras.id"))
    ativo_id: Mapped[int] = mapped_column(Integer, ForeignKey("invest_ativos.id"))
    quantidade: Mapped[Decimal] = mapped_column(Numeric(24, 10), default=Decimal("0"))
    preco_medio: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))  # moeda original
    custo_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))  # moeda original
    custo_total_brl: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    carteira: Mapped[InvestCarteira] = relationship(back_populates="posicoes")
    ativo: Mapped[InvestAtivo] = relationship()
    lotes: Mapped[list["InvestLote"]] = relationship(back_populates="posicao")


# ──────────────────────────────────────────────
# Lotes (controle detalhado)
# ──────────────────────────────────────────────
class InvestLote(Base):
    __tablename__ = "invest_lotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    posicao_id: Mapped[int] = mapped_column(Integer, ForeignKey("invest_posicoes.id"))
    movimento_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("invest_movimentacoes.id"), nullable=True)
    data_aquisicao: Mapped[date] = mapped_column(Date)
    quantidade: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    quantidade_aberta: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    preco_unitario: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    custo_total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    taxa_cambio: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8), nullable=True)
    custo_total_brl: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    corretora_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # ref invest_instituicoes

    posicao: Mapped[InvestPosicao] = relationship(back_populates="lotes")


# ──────────────────────────────────────────────
# Movimentações / Operações
# ──────────────────────────────────────────────
class InvestMovimentacao(Base):
    __tablename__ = "invest_movimentacoes"
    __table_args__ = (Index("ix_invest_mov_data", "data_negociacao"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    carteira_id: Mapped[int] = mapped_column(Integer, ForeignKey("invest_carteiras.id"))
    ativo_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("invest_ativos.id"), nullable=True)
    # APORTE, RESGATE, COMPRA, VENDA, TRANSFERENCIA, AJUSTE, RENDIMENTO, EVENTO
    tipo: Mapped[str] = mapped_column(String(30))
    data_negociacao: Mapped[date] = mapped_column(Date)
    data_liquidacao: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    quantidade: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 10), nullable=True)
    preco_unitario: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8), nullable=True)
    valor_bruto: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    taxas_custos: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    valor_liquido: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))  # moeda original
    moeda_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # ref contas_moedas
    taxa_cambio: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8), nullable=True)
    valor_brl: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    corretora_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # ref invest_instituicoes
    # referência à operação financeira integrada (operacoes.operacoes_id), sem FK direta
    operacao_financeira_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    id_correlacao: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # idempotência/ligação
    observacoes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    carteira: Mapped[InvestCarteira] = relationship()
    ativo: Mapped[Optional[InvestAtivo]] = relationship()

    __table_args__ = (
        Index("ix_invest_mov_data", "data_negociacao"),
        Index("ix_invest_mov_correlacao", "id_correlacao"),
    )


# ──────────────────────────────────────────────
# Eventos (proventos, FII, corporativos)
# ──────────────────────────────────────────────
class InvestEvento(Base):
    __tablename__ = "invest_eventos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    carteira_id: Mapped[int] = mapped_column(Integer, ForeignKey("invest_carteiras.id"))
    ativo_id: Mapped[int] = mapped_column(Integer, ForeignKey("invest_ativos.id"))
    # DIVIDENDO, JCP, RENDIMENTO_FII, AMORTIZACAO, BONIFICACAO, DESDOBRAMENTO, GRUPAMENTO, SUBSCRICAO, VENCIMENTO, OUTRO
    tipo_evento: Mapped[str] = mapped_column(String(40))
    data_com: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    data_pagamento: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    quantidade_base: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 10), nullable=True)
    valor_unitario: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8), nullable=True)
    valor_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))  # moeda original
    imposto_retido: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    moeda_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    taxa_cambio: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8), nullable=True)
    valor_brl: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    impacto_posicao: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # CAIXA, QUANTIDADE, NENHUM
    observacoes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    carteira: Mapped[InvestCarteira] = relationship()
    ativo: Mapped[InvestAtivo] = relationship()


# ──────────────────────────────────────────────
# Registro fiscal de apoio
# ──────────────────────────────────────────────
class InvestRegistroFiscal(Base):
    __tablename__ = "invest_registros_fiscais"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    carteira_id: Mapped[int] = mapped_column(Integer, ForeignKey("invest_carteiras.id"))
    ativo_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("invest_ativos.id"), nullable=True)
    movimento_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("invest_movimentacoes.id"), nullable=True)
    evento_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("invest_eventos.id"), nullable=True)
    competencia: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # YYYY-MM
    categoria_tributaria: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # RV, RF, FII, etc.
    base_calculo: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    imposto_devido: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    imposto_retido: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    aliquota: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4), nullable=True)
    dados_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # detalhes adicionais rastreáveis
    observacoes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
