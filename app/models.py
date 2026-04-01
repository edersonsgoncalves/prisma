"""
models.py — Mapeamento SQLAlchemy das tabelas MySQL do FinOrg.
Refatorado para o novo schema (FinOrg_Prod) com padronização snake_case e Foreign Keys.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    BigInteger, Date, DateTime, ForeignKey,
    Integer, Numeric, SmallInteger, String, Text
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ──────────────────────────────────────────────
# TIPOS DE OPERAÇÃO  (tipo_operacao_id: 1=Receita, 2=Investimento, 3=Despesa, 4=Transferência)
# ──────────────────────────────────────────────
class TipoOperacao(Base):
    __tablename__ = "tipos_operacoes"

    tipo_operacao_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo_operacao_nome: Mapped[str] = mapped_column(String(50))

    operacoes: Mapped[List["Operacao"]] = relationship(back_populates="tipo")


# ──────────────────────────────────────────────
# CATEGORIAS
# ──────────────────────────────────────────────
class Categoria(Base):
    __tablename__ = "categorias"

    categorias_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    categorias_nome: Mapped[str] = mapped_column(String(100))
    categorias_classe: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    categorias_pai_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("categorias.categorias_id"), nullable=True
    )

    # Relacionamentos de hierarquia
    pai: Mapped[Optional["Categoria"]] = relationship(
        "Categoria", remote_side=[categorias_id], back_populates="subcategorias_lista"
    )
    subcategorias_lista: Mapped[List["Categoria"]] = relationship(
        "Categoria", back_populates="pai"
    )

    # Relacionamento de visualização para operações
    operacoes: Mapped[List["Operacao"]] = relationship(
        "Operacao",
        primaryjoin="Operacao.operacoes_categoria == Categoria.categorias_id",
        foreign_keys="Operacao.operacoes_categoria",
        viewonly=True,
    )


# ──────────────────────────────────────────────
# CONTAS BANCÁRIAS  (tipo_conta: 1=CC, 2=Poupança, 3=Carteira, 4=Cartão)
# ──────────────────────────────────────────────
class ContaBancaria(Base):
    __tablename__ = "contas_bancarias"

    conta_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome_conta: Mapped[str] = mapped_column(String(100))
    tipo_conta: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    conta_moeda: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    contas_limite: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    contas_cartao_fechamento: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    contas_prev_debito: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    contas_desconsiderar_saldo: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    operacoes: Mapped[List["Operacao"]] = relationship(back_populates="conta")
    faturas: Mapped[List["FaturaCartao"]] = relationship(back_populates="cartao")
    adicionais: Mapped[List["CartaoAdicional"]] = relationship(
        back_populates="conta_mestre",
        foreign_keys="[CartaoAdicional.conta_id]"
    )


# ──────────────────────────────────────────────
# FATURAS DE CARTÃO
# ──────────────────────────────────────────────
class FaturaCartao(Base):
    __tablename__ = "faturas_cartoes"

    fatura_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conta_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contas_bancarias.conta_id"), nullable=True
    )
    data_vencimento: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    data_fechamento: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    fechado: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True, default=0)
    valor_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    mes_referencia: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    cartao: Mapped[Optional[ContaBancaria]] = relationship(back_populates="faturas")
    operacoes: Mapped[List["Operacao"]] = relationship(back_populates="fatura")


# ──────────────────────────────────────────────
# CARTÕES ADICIONAIS (Portadores)
# ──────────────────────────────────────────────
class CartaoAdicional(Base):
    __tablename__ = "cartoes_adicionais"

    adicional_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conta_id: Mapped[int] = mapped_column(Integer, ForeignKey("contas_bancarias.conta_id"))
    adicional_nome: Mapped[str] = mapped_column(String(100))
    cartao_final: Mapped[str] = mapped_column(String(10))
    apelido: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    conta_vinculada: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contas_bancarias.conta_id"), nullable=True
    )
    titular: Mapped[int] = mapped_column(SmallInteger, default=0)
    ativo: Mapped[int] = mapped_column(SmallInteger, default=1)

    # Relacionamentos
    conta_mestre: Mapped["ContaBancaria"] = relationship(
        "ContaBancaria", foreign_keys=[conta_id]
    )
    conta_destino: Mapped[Optional["ContaBancaria"]] = relationship(
        "ContaBancaria", foreign_keys=[conta_vinculada]
    )


# ──────────────────────────────────────────────
# RECORRÊNCIAS
# ──────────────────────────────────────────────
class Recorrencia(Base):
    __tablename__ = "recorrencias"

    recorrencia_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    frequencia: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    dias_uteis: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    operacoes: Mapped[List["Operacao"]] = relationship(back_populates="recorrencia_obj")


# ──────────────────────────────────────────────
# PROJETOS
# ──────────────────────────────────────────────
class Projeto(Base):
    __tablename__ = "projetos"

    projeto_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    projetos_nome: Mapped[str] = mapped_column(String(150))
    projetos_inicio: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    projetos_fim: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    projetos_cor: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)

    operacoes: Mapped[List["Operacao"]] = relationship(back_populates="projeto")


# ──────────────────────────────────────────────
# OPERAÇÕES (tabela principal)
# ──────────────────────────────────────────────
class Operacao(Base):
    __tablename__ = "operacoes"

    operacoes_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    operacoes_data_lancamento: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    operacoes_descricao: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    operacoes_conta: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contas_bancarias.conta_id"), nullable=True
    )
    operacoes_valor: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    operacoes_tipo: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tipos_operacoes.tipo_operacao_id"), nullable=True
    )
    operacoes_transf_rel: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    operacoes_categoria: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("categorias.categorias_id"), nullable=True
    )
    operacoes_parcela: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    operacoes_fatura: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("faturas_cartoes.fatura_id"), nullable=True
    )
    operacoes_recorrencia: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("recorrencias.recorrencia_id"), nullable=True
    )
    operacoes_fitid: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    operacoes_data_efetivado: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    operacoes_efetivado: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    operacoes_validacao: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    operacoes_projeto: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("projetos.projeto_id"), nullable=True
    )
    operacoes_grupo_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    operacoes_adicional_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("cartoes_adicionais.adicional_id"), nullable=True
    )

    # Relacionamentos
    conta: Mapped[Optional[ContaBancaria]] = relationship(back_populates="operacoes")
    tipo: Mapped[Optional[TipoOperacao]] = relationship(back_populates="operacoes")
    fatura: Mapped[Optional[FaturaCartao]] = relationship(back_populates="operacoes")
    recorrencia_obj: Mapped[Optional[Recorrencia]] = relationship(back_populates="operacoes")
    projeto: Mapped[Optional[Projeto]] = relationship(back_populates="operacoes")
    adicional: Mapped[Optional[CartaoAdicional]] = relationship()
    categoria_obj: Mapped[Optional[Categoria]] = relationship(
        "Categoria",
        primaryjoin="Operacao.operacoes_categoria == Categoria.categorias_id",
        foreign_keys=[operacoes_categoria],
    )
    transf_rel_obj: Mapped[Optional["Operacao"]] = relationship(
        "Operacao",
        primaryjoin="Operacao.operacoes_transf_rel == Operacao.operacoes_id",
        foreign_keys=[operacoes_transf_rel],
        remote_side=[operacoes_id],
        viewonly=True,
    )


# ──────────────────────────────────────────────
# USUÁRIOS
# ──────────────────────────────────────────────
class Usuario(Base):
    __tablename__ = "usuarios"

    usuario_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_login: Mapped[str] = mapped_column(String(100), unique=True)
    usuario_senha: Mapped[str] = mapped_column(String(255))  # bcrypt hash
    usuario_nome: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    usuario_ativo: Mapped[int] = mapped_column(SmallInteger, default=1)


# ──────────────────────────────────────────────
# LOGS DE OPERAÇÃO E NOTIFICAÇÕES
# ──────────────────────────────────────────────
class LogOperacao(Base):
    __tablename__ = "logs_operacoes"

    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    log_timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    log_usuario_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("usuarios.usuario_id"), nullable=True
    )
    log_acao: Mapped[str] = mapped_column(String(50))  # INSERT, UPDATE, DELETE, SYSTEM
    log_entidade: Mapped[str] = mapped_column(String(50))  # OPERACAO, FATURA, CATEGORIA, etc
    log_entidade_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    log_detalhes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    log_lido: Mapped[int] = mapped_column(SmallInteger, default=0)

    usuario: Mapped[Optional[Usuario]] = relationship()
