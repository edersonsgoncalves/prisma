"""Serviços do módulo de investimentos (cadastros, câmbio, integração)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import uuid

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

# ── Tipos e semântica de movimentação ───────────────────────────────────────
# Caixa/fronteira (impacta caixa da carteira e pode integrar com financeiro):
TIPOS_CAIXA_ENTRADA = {"APORTE_CAIXA", "APORTE"}  # APORTE = legado
TIPOS_CAIXA_SAIDA = {"RETIRADA_CAIXA", "RESGATE"}  # RESGATE = legado (retirada para conta externa)
# Operações em ativo (impactam posição e caixa da carteira):
TIPOS_ATIVO_ENTRADA = {"APLICACAO", "COMPRA", "APORTE_ATIVO"}  # aplicação/compra (consome caixa)
TIPOS_ATIVO_SAIDA = {"RESGATE_ATIVO", "VENDA", "VENCIMENTO"}  # resgate/venda/vencimento (gera caixa)
TIPOS_AJUSTE = {"AJUSTE"}

TIPOS_INTEGRACAO_CAIXA = TIPOS_CAIXA_ENTRADA | TIPOS_CAIXA_SAIDA
TIPOS_ATIVO = TIPOS_ATIVO_ENTRADA | TIPOS_ATIVO_SAIDA


def tipo_label(tipo: Optional[str]) -> str:
    t = (tipo or "").upper()
    labels = {
        "APORTE_CAIXA": "Aporte de caixa (carteira)",
        "APORTE": "Aporte de caixa (legado)",
        "RETIRADA_CAIXA": "Retirada de caixa para conta externa",
        "RESGATE": "Retirada de caixa (legado)",
        "APLICACAO": "Aplicação em ativo",
        "COMPRA": "Compra de ativo",
        "APORTE_ATIVO": "Aplicação em ativo",
        "RESGATE_ATIVO": "Resgate de ativo",
        "VENDA": "Venda de ativo",
        "VENCIMENTO": "Vencimento de ativo",
        "AJUSTE": "Ajuste",
    }
    return labels.get(t, t or "-")


def calcular_saldo_caixa_carteira(db: Session, carteira_id: int) -> Decimal:
    """Saldo de caixa explícito (derivado) da carteira.

    Regras:
    - Aporte de caixa: +valor (entrada na carteira).
    - Retirada de caixa: -valor (saída da carteira).
    - Aplicação/compra de ativo: -valor (consome caixa da carteira), exceto quando financiada
      diretamente pela conta externa (integração financeira) — nesse caso o efeito líquido no caixa é nulo.
    - Resgate/venda/vencimento de ativo: +valor (gera caixa na carteira), exceto quando enviado
      diretamente para conta externa (integração financeira) — efeito líquido nulo no caixa.
    - Ajuste: sem efeito automático no caixa por padrão.
    """
    movs = db.query(InvestMovimentacao).filter(InvestMovimentacao.carteira_id == carteira_id).all()
    saldo = Decimal("0")
    for m in movs:
        t = (m.tipo or "").upper()
        val = Decimal(str(m.valor_liquido or m.valor_bruto or 0))
        integrado = bool(m.operacao_financeira_id)
        if t in TIPOS_CAIXA_ENTRADA:
            saldo += val
        elif t in TIPOS_CAIXA_SAIDA:
            saldo -= val
        elif t in TIPOS_ATIVO_ENTRADA:
            # consome caixa, salvo se financiado direto externamente (fluxo único)
            if not integrado:
                saldo -= val
        elif t in TIPOS_ATIVO_SAIDA:
            # gera caixa, salvo se enviado direto externamente
            if not integrado:
                saldo += val
        # AJUSTE: sem efeito automático no caixa
    return saldo


class IntegracaoFinanceiraService:
    """Integração entre investimentos e finanças pessoais.

    Fronteira de caixa como transferências (Operacao tipo 4), idempotentes e rastreáveis.
    """

    @staticmethod
    def _buscar_mov_integrada(db: Session, id_correlacao: str, ignorar_mov_id: Optional[int] = None) -> Optional[InvestMovimentacao]:
        q = db.query(InvestMovimentacao).filter(
            InvestMovimentacao.id_correlacao == id_correlacao,
            InvestMovimentacao.operacao_financeira_id.isnot(None),
        )
        if ignorar_mov_id:
            q = q.filter(InvestMovimentacao.id != ignorar_mov_id)
        return q.first()

    @staticmethod
    def _criar_transferencia(db: Session, conta_origem_id: int, conta_destino_id: int, valor: Decimal, data_mov: date, descricao: str, grupo_id: str, efetivado: int = 1) -> Operacao:
        dt_efet = datetime.combine(data_mov, datetime.min.time()) if efetivado else None
        op_saida = Operacao(
            operacoes_data_lancamento=data_mov,
            operacoes_descricao=descricao,
            operacoes_conta=conta_origem_id,
            operacoes_valor=-abs(valor),
            operacoes_tipo=4,
            operacoes_efetivado=1 if efetivado else 0,
            operacoes_data_efetivado=dt_efet,
            operacoes_validacao=1,
            operacoes_grupo_id=grupo_id,
        )
        op_entrada = Operacao(
            operacoes_data_lancamento=data_mov,
            operacoes_descricao=descricao,
            operacoes_conta=conta_destino_id,
            operacoes_valor=abs(valor),
            operacoes_tipo=4,
            operacoes_efetivado=1 if efetivado else 0,
            operacoes_data_efetivado=dt_efet,
            operacoes_validacao=1,
            operacoes_grupo_id=grupo_id,
        )
        db.add(op_saida)
        db.add(op_entrada)
        db.flush()
        op_saida.operacoes_transf_rel = op_entrada.operacoes_id
        op_entrada.operacoes_transf_rel = op_saida.operacoes_id
        return op_saida

    @staticmethod
    def integrar_transferencia_caixa(db: Session, mov: InvestMovimentacao, conta_externa_id: int, efetivado: int = 1, sentido: Optional[str] = None) -> dict:
        """Cria transferência financeira idempotente para entrada/saída de caixa da carteira.

        sentido: 'ENTRADA' (externa -> carteira) ou 'SAIDA' (carteira -> externa). Se None, inferido do tipo.
        """
        tipo = (mov.tipo or "").upper()
        if sentido is None:
            if tipo in TIPOS_CAIXA_ENTRADA or tipo in TIPOS_ATIVO_ENTRADA:
                sentido = "ENTRADA"
            elif tipo in TIPOS_CAIXA_SAIDA or tipo in TIPOS_ATIVO_SAIDA:
                sentido = "SAIDA"
            else:
                return {"ok": False, "erro": "Tipo de movimentação não elegível para integração financeira."}

        carteira = db.query(InvestCarteira).filter(InvestCarteira.id == mov.carteira_id).first()
        if not carteira or not carteira.conta_financeira_id:
            return {"ok": False, "erro": "Carteira sem conta financeira vinculada para integração."}
        if int(conta_externa_id) == int(carteira.conta_financeira_id):
            return {"ok": False, "erro": "Conta externa é a mesma da carteira; transferência tipo 4 não se aplica. Use uma conta externa diferente ou registre sem integração financeira automática."}

        if not mov.id_correlacao:
            mov.id_correlacao = f"INV-{tipo}-{uuid.uuid4().hex[:12]}"

        existente = IntegracaoFinanceiraService._buscar_mov_integrada(db, mov.id_correlacao, ignorar_mov_id=mov.id)
        if existente:
            mov.operacao_financeira_id = existente.operacao_financeira_id
            return {"ok": True, "operacao_financeira_id": existente.operacao_financeira_id, "id_correlacao": mov.id_correlacao, "reutilizado": True}

        valor = Decimal(str(mov.valor_liquido or mov.valor_bruto or 0))
        if valor <= 0:
            return {"ok": False, "erro": "Valor inválido para integração."}

        if sentido == "ENTRADA":
            conta_origem_id = int(conta_externa_id)
            conta_destino_id = int(carteira.conta_financeira_id)
            if tipo in TIPOS_ATIVO_ENTRADA:
                descricao = f"Aplicação investimento (direto) - {carteira.nome}"
            else:
                descricao = f"Aporte investimento - {carteira.nome}"
        else:  # SAIDA
            conta_origem_id = int(carteira.conta_financeira_id)
            conta_destino_id = int(conta_externa_id)
            if tipo in TIPOS_ATIVO_SAIDA:
                descricao = f"Resgate de ativo (direto) - {carteira.nome}"
            else:
                descricao = f"Retirada investimento - {carteira.nome}"

        grupo_id = f"INVTRF-{uuid.uuid4().hex[:10]}"
        op_saida = IntegracaoFinanceiraService._criar_transferencia(
            db, conta_origem_id, conta_destino_id, abs(valor), mov.data_negociacao, descricao, grupo_id, efetivado=efetivado
        )
        mov.operacao_financeira_id = op_saida.operacoes_id
        return {"ok": True, "operacao_financeira_id": op_saida.operacoes_id, "id_correlacao": mov.id_correlacao, "reutilizado": False}


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
    integrar_financeiro: bool = False,
    conta_externa_id: Optional[int] = None,
    efetivado: int = 1,
) -> tuple[InvestMovimentacao, Optional[dict]]:
    """Registra movimentação separando caixa/fronteira e operação em ativo.

    - APORTE_CAIXA/RETIRADA_CAIXA (e legados APORTE/RESGATE): movimentam caixa da carteira e podem integrar
      com financeiro (transferência tipo 4 entre conta externa e conta vinculada da carteira).
    - APLICACAO/COMPRA: consomem caixa da carteira e aumentam posição no ativo. Podem ser financiadas
      diretamente pela conta externa (integração cria transferência externa->carteira para este fluxo).
    - RESGATE_ATIVO/VENDA/VENCIMENTO: reduzem posição e geram caixa na carteira. Podem, opcionalmente,
      enviar recursos direto para conta externa (integração carteira->externa).
    """
    tipo_u = (tipo or "").upper()
    # normalizações de legados/aliases para consistência interna de UI
    if tipo_u == "APORTE":
        tipo_u = "APORTE_CAIXA"
    elif tipo_u == "RESGATE":
        tipo_u = "RETIRADA_CAIXA"

    qtd = _to_decimal(quantidade)
    pu = _to_decimal(preco_unitario)
    bruto = _to_decimal(valor_bruto)
    custos = _to_decimal(taxas_custos)
    if bruto == 0 and qtd != 0 and pu != 0:
        bruto = (qtd * pu).quantize(Decimal("0.01"))

    # Valor líquido: saídas de valor (venda/resgate de ativo, retirada de caixa) líquidas de custos;
    # entradas/aplicações consideram custo total (valor + custos) quando aplicável.
    if tipo_u in TIPOS_ATIVO_SAIDA or tipo_u in TIPOS_CAIXA_SAIDA:
        liquido = (bruto - custos) if bruto != 0 else bruto
    else:
        liquido = (bruto + custos) if bruto != 0 else bruto
    if liquido == 0:
        liquido = bruto

    valor_brl = None
    if taxa_cambio is not None:
        try:
            valor_brl = (liquido * Decimal(str(taxa_cambio))).quantize(Decimal("0.01"))
        except Exception:
            valor_brl = None

    carteira = db.query(InvestCarteira).filter(InvestCarteira.id == carteira_id).first()
    if not carteira:
        raise ValueError("Carteira não encontrada.")

    # Validações de domínio
    if tipo_u in TIPOS_ATIVO and not ativo_id:
        raise ValueError("Selecione o ativo/produto para operações de aplicação/compra ou resgate/venda.")
    if tipo_u in TIPOS_ATIVO and _to_decimal(qtd) == 0:
        raise ValueError("Informe a quantidade para operações em ativo.")

    if tipo_u in TIPOS_CAIXA_ENTRADA | TIPOS_CAIXA_SAIDA:
        if _to_decimal(liquido) <= 0 and _to_decimal(bruto) > 0:
            liquido = bruto
        if _to_decimal(liquido) <= 0:
            raise ValueError("Informe um valor válido para movimentação de caixa.")
    if tipo_u in TIPOS_ATIVO and _to_decimal(liquido) <= 0 and _to_decimal(bruto) > 0:
        liquido = bruto

    integracao_result = None
    sentido_integracao = None
    # Integração financeira: caixa (fronteira) ou operações em ativo financiadas/dirigidas direto pela conta externa
    if integrar_financeiro:
        if not conta_externa_id:
            raise ValueError("Selecione a conta financeira externa para integração.")
        if not carteira.conta_financeira_id:
            raise ValueError("Carteira sem conta financeira vinculada para integração.")
        if int(conta_externa_id) == int(carteira.conta_financeira_id):
            raise ValueError("Conta externa é a mesma da conta vinculada da carteira. Para esse caso (conta integrada), registre sem transferência automática ou ajuste o vínculo/fluxo.")
        if tipo_u in TIPOS_CAIXA_ENTRADA or tipo_u in TIPOS_ATIVO_ENTRADA:
            sentido_integracao = "ENTRADA"  # externa -> carteira (aporte ou financiamento direto da aplicação)
        elif tipo_u in TIPOS_CAIXA_SAIDA or tipo_u in TIPOS_ATIVO_SAIDA:
            sentido_integracao = "SAIDA"  # carteira -> externa (retirada ou resgate direto)
        else:
            raise ValueError("Tipo de movimentação não elegível para integração financeira.")
        if _to_decimal(liquido) <= 0:
            raise ValueError("Informe um valor válido para integrar com o financeiro.")

    mov = InvestMovimentacao(
        carteira_id=carteira_id,
        ativo_id=ativo_id,
        tipo=tipo_u,
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
    if ativo_id and qtd != 0 and tipo_u in TIPOS_ATIVO:
        pos = obter_ou_criar_posicao(db, carteira_id, ativo_id)
        if tipo_u in TIPOS_ATIVO_ENTRADA:
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
        elif tipo_u in TIPOS_ATIVO_SAIDA:
            pos.quantidade = (Decimal(str(pos.quantidade or 0)) - qtd)
            custo_saida = (Decimal(str(pos.preco_medio or 0)) * qtd)
            pos.custo_total = (Decimal(str(pos.custo_total or 0)) - custo_saida)
            if pos.quantidade <= 0:
                pos.quantidade = Decimal("0")
                pos.custo_total = Decimal("0")
                pos.preco_medio = Decimal("0")
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

    # Integração financeira (transferência de fronteira)
    if integrar_financeiro and conta_externa_id and sentido_integracao:
        if _to_decimal(mov.valor_liquido) <= 0 and _to_decimal(mov.valor_bruto) > 0:
            mov.valor_liquido = mov.valor_bruto
        integracao_result = IntegracaoFinanceiraService.integrar_transferencia_caixa(
            db, mov, int(conta_externa_id), efetivado=1 if efetivado else 0, sentido=sentido_integracao
        )
        if not integracao_result.get("ok"):
            db.rollback()
            raise ValueError(integracao_result.get("erro") or "Falha ao integrar com o financeiro.")
        db.add(mov)

    db.commit()
    db.refresh(mov)
    return mov, integracao_result
