# app/helpers/ofx_tools.py
"""
Lógica de negócio para importação OFX.
Integra com:
  - app/ofx_importer.py   (OFXReader já existente)
  - tabela operacoes       (modelo Operacao)
  - tabela contas_bancarias (modelo ContaBancaria)

ATENÇÃO — migration necessária:
  A coluna `operacoes_fitid` ainda não existe na tabela `operacoes`.
  Execute antes de usar:

    ALTER TABLE operacoes
      ADD COLUMN operacoes_fitid VARCHAR(255) NULL DEFAULT NULL
      AFTER operacoes_grupo_id;
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import Operacao, ContaBancaria
from app.ofx_importer import OFXParser


# ═══════════════════════════════════════════════════════════════════════════
# PESOS E LIMIARES
# ═══════════════════════════════════════════════════════════════════════════
SCORE_FITID_MATCH = 100
SCORE_VALOR_EXATO = 45
SCORE_VALOR_MUITO_PROXIMO = 15  # diferença mínima (centavos/arredondamento)
SCORE_DATA_EXATA  = 30
SCORE_DATA_1DIA   = 20
SCORE_DATA_2A3DIAS = 10
SCORE_DATA_4A7DIAS = 3
SCORE_MEMO_ALTO   = 15  # similaridade >= 0.85
SCORE_MEMO_MEDIO  = 8   # similaridade >= 0.70
SCORE_TIPO_IGUAL  = 10
PENALIDADE_TIPO   = -60

THRESHOLD_DUP    = 100
THRESHOLD_FORTE  = 75
THRESHOLD_FRACO  = 45

JANELA_DIAS = 10
VALOR_TOLERANCIA_PRE_FILTRO_ABS = 1.00
VALOR_TOLERANCIA_PRE_FILTRO_PCT = 0.10  # 10% (apenas para pré-seleção de candidatos)

# Mapeamento tipo OFX (int) → D/C
# operacoes_tipo: 1=Receita, 3=Despesa, 4=Transferência (conforme extrato.html)
TIPO_INT_PARA_DC = {1: "C", 3: "D", 4: "D"}


# ═══════════════════════════════════════════════════════════════════════════
# SCHEMA DO PAYLOAD
# ═══════════════════════════════════════════════════════════════════════════
class EfetivarPayload(BaseModel):
    acao: str               # "efetivar" | "inserir" | "forcar" | "ignorar"
    fitid: str
    data: str               # ISO: YYYY-MM-DD
    valor: float
    tipo: str               # "C" crédito | "D" débito
    memo: Optional[str] = None
    match_id: Optional[int] = None   # operacoes_id do lançamento existente
    conta_id: Optional[int] = None   # conta_id de contas_bancarias
    como_efetivado: bool = True      # status de conciliação


# ═══════════════════════════════════════════════════════════════════════════
# FUNÇÕES PRINCIPAIS
# ═══════════════════════════════════════════════════════════════════════════

def analisar_ofx(
    filepath: str,
    db: Session,
    conta_id: Optional[int] = None,
) -> dict:
    """
    Parse do arquivo OFX + matching contra tabela operacoes.
    Retorna JSON pronto para o frontend.
    """
    reader = OFXParser()
    transacoes_raw = reader.parse(filepath)

    resultado = []
    for t in transacoes_raw:
        ofx = _normalizar(t)
        candidatos = _buscar_candidatos(ofx, db, conta_id)
        melhor, score, detalhes = _melhor_match(ofx, candidatos)
        classif = _classificar(score)

        resultado.append({
            "fitid":         ofx["fitid"],
            "data":          ofx["data"].isoformat(),
            "valor":         float(ofx["valor"]),
            "tipo":          ofx["tipo"],
            "memo":          ofx["memo"],
            "score":         score,
            "classificacao": classif,
            "scoreDetalhes": detalhes,
            "matchDb":       _serializar(melhor) if melhor else None,
        })

    return {"transacoes": resultado}


def efetivar_transacao(payload: EfetivarPayload, db: Session) -> dict:
    """
    Aplica a ação definida pelo usuário.

    efetivar / forcar → atualiza Operacao existente:
                          operacoes_efetivado = 1
                          operacoes_data_efetivado = data do OFX
                          operacoes_fitid = fitid do OFX

    inserir           → cria nova Operacao já efetivada

    ignorar           → noop
    """
    if payload.acao == "ignorar":
        return {"status": "ignorado"}

    data_lancamento = date.fromisoformat(payload.data)
    # C=Receita(1) / D=Despesa(3)
    tipo_int = 1 if payload.tipo == "C" else 3

    # ── EFETIVAR / FORÇAR ─────────────────────────────────────────────────
    if payload.acao in ("efetivar", "forcar"):
        if not payload.match_id:
            raise ValueError("match_id é obrigatório para ação 'efetivar'.")

        op = db.query(Operacao).filter(
            Operacao.operacoes_id == payload.match_id
        ).first()

        if not op:
            raise ValueError(f"Operação {payload.match_id} não encontrada.")

        # Sobrescrever data e valor sempre com os dados do OFX:
        op.operacoes_data_lancamento = data_lancamento
        op.operacoes_valor = -abs(payload.valor) if payload.tipo == "D" else abs(payload.valor)

        # Concatenar a descrição do OFX (até 20 caracteres)
        memo_str = (payload.memo or "").strip()[:20]
        if memo_str and f"({memo_str})" not in (op.operacoes_descricao or ""):
            nova_desc = f"{op.operacoes_descricao or ''} ({memo_str})".strip()
            op.operacoes_descricao = nova_desc[:255]

        op.operacoes_efetivado        = 1 if payload.como_efetivado else 0
        if payload.como_efetivado:
            op.operacoes_data_efetivado = datetime.combine(data_lancamento, datetime.min.time())
        else:
            op.operacoes_data_efetivado = None
        op.operacoes_fitid            = payload.fitid   # requer migration (ver docstring)
        db.commit()
        return {"status": "efetivado", "id": op.operacoes_id}

    # ── INSERIR ───────────────────────────────────────────────────────────
    if payload.acao == "inserir":
        if payload.conta_id:
            conta = db.query(ContaBancaria).filter(
                ContaBancaria.conta_id == payload.conta_id
            ).first()
            if not conta:
                raise ValueError("Conta não encontrada.")
        else:
            conta = db.query(ContaBancaria).first()
            if not conta:
                raise ValueError("Nenhuma conta cadastrada no sistema.")

        valor_op = -abs(payload.valor) if payload.tipo == "D" else abs(payload.valor)
        nova = Operacao(
            operacoes_data_lancamento = data_lancamento,
            operacoes_descricao       = payload.memo or "Importado via OFX",
            operacoes_conta           = conta.conta_id,
            operacoes_valor           = valor_op,
            operacoes_tipo            = tipo_int,
            operacoes_efetivado       = 1 if payload.como_efetivado else 0,
            operacoes_data_efetivado  = datetime.combine(data_lancamento, datetime.min.time()) if payload.como_efetivado else None,
            operacoes_fitid           = payload.fitid,
            operacoes_validacao       = 1,
        )
        db.add(nova)
        db.commit()
        db.refresh(nova)
        return {"status": "inserido", "id": nova.operacoes_id}

    raise ValueError(f"Ação desconhecida: {payload.acao}")


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS INTERNOS
# ═══════════════════════════════════════════════════════════════════════════

TRNTYPE_MAP = {
    "DEBIT": "D", "CREDIT": "C", "INT": "C", "DIV": "C",
    "FEE": "D", "SRVCHG": "D", "DEP": "C", "ATM": "D",
    "POS": "D", "XFER": "D", "CHECK": "D", "PAYMENT": "D",
    "CASH": "D", "DIRECTDEP": "C", "DIRECTDEBIT": "D",
    "REPEATPMT": "D", "OTHER": "D",
}


def _normalizar(t: dict) -> dict:
    """Padroniza o dict vindo do OFXParser para campos internos.

    Ordem de prioridade para determinar D/C:
      1. Campo 'tipo' já calculado pelo OFXParser (mais confiável)
      2. Campo TRNTYPE presente no dict bruto
      3. Sinal do valor (fallback)
    """
    valor_raw = float(t.get("valor") or t.get("TRNAMT") or 0)
    valor = abs(valor_raw)

    # 1. OFXParser já resolveu o tipo — usa direto
    if t.get("tipo") in ("C", "D"):
        tipo = t["tipo"]
    # 2. TRNTYPE presente no dict bruto (parsing manual / outro fonte)
    elif t.get("TRNTYPE"):
        trntype = str(t["TRNTYPE"]).upper().strip()
        tipo = TRNTYPE_MAP.get(trntype, "D" if valor_raw < 0 else "C")
    # 3. Fallback: sinal do valor
    else:
        tipo = "D" if valor_raw < 0 else "C"

    data_raw = t.get("data") or t.get("DTPOSTED") or t.get("date")
    if isinstance(data_raw, str):
        nums = data_raw[:10].replace("/", "-")
        if len(nums) == 8 and "-" not in nums:
            nums = f"{nums[:4]}-{nums[4:6]}-{nums[6:8]}"
        data = date.fromisoformat(nums)
    elif isinstance(data_raw, datetime):
        data = data_raw.date()
    elif isinstance(data_raw, date):
        data = data_raw
    else:
        data = date.today()

    memo  = str(t.get("memo") or t.get("MEMO") or t.get("NAME") or "")
    memo  = " ".join(memo.split())[:255]
    fitid = str(t.get("fitid") or t.get("FITID") or "")

    return {"fitid": fitid, "tipo": tipo, "valor": valor, "data": data, "memo": memo}


def _buscar_candidatos(
    ofx: dict, db: Session, conta_id: Optional[int]
) -> list[Operacao]:
    """Busca Operacoes na janela de datas (e conta, se informada) com valor plausível."""
    data_min = ofx["data"] - timedelta(days=JANELA_DIAS)
    data_max = ofx["data"] + timedelta(days=JANELA_DIAS)

    q = db.query(Operacao).filter(
        Operacao.operacoes_data_lancamento >= data_min,
        Operacao.operacoes_data_lancamento <= data_max,
        Operacao.operacoes_validacao == 1,
    )
    if conta_id:
        q = q.filter(Operacao.operacoes_conta == conta_id)

    candidatos = q.all()
    v_ofx = abs(float(ofx.get("valor") or 0))
    limite = max(VALOR_TOLERANCIA_PRE_FILTRO_ABS, v_ofx * VALOR_TOLERANCIA_PRE_FILTRO_PCT)
    plausiveis = []
    for op in candidatos:
        v_db = abs(float(op.operacoes_valor or 0))
        if abs(v_db - v_ofx) <= limite:
            plausiveis.append(op)
    return plausiveis


def _similaridade_memo(memo_ofx: str, memo_db: str) -> float:
    a = " ".join((memo_ofx or "").lower().split())
    b = " ".join((memo_db or "").lower().split())
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _calcular_score(ofx: dict, op: Operacao) -> tuple[int, dict]:
    detalhes = {
        "fitid": "diferente",
        "valor": None,
        "data_dias": None,
        "tipo": None,
        "memo_ratio": 0.0,
        "regras": [],
    }

    # FITID idêntico → duplicidade certa, FITID diferente → já vinculado a outro, anula match
    fitid_db = getattr(op, "operacoes_fitid", None)
    if fitid_db:
        if fitid_db == ofx["fitid"]:
            detalhes["fitid"] = "igual"
            detalhes["regras"].append("FITID idêntico (duplicidade)")
            return SCORE_FITID_MATCH, detalhes
        detalhes["fitid"] = "vinculado_outro"
        detalhes["regras"].append("Lançamento já vinculado a outro FITID")
        return 0, detalhes
    detalhes["fitid"] = "ausente"

    score = 0

    # Valor (tolerância estrita para evitar falsos positivos)
    v_db = abs(float(op.operacoes_valor or 0))
    v_ofx = abs(float(ofx.get("valor") or 0))
    diff_valor = abs(v_db - v_ofx)
    valor_exato = diff_valor < 0.01
    valor_muito_proximo = (not valor_exato) and diff_valor <= 0.05
    if valor_exato:
        score += SCORE_VALOR_EXATO
        detalhes["valor"] = "exato"
    elif valor_muito_proximo:
        score += SCORE_VALOR_MUITO_PROXIMO
        detalhes["valor"] = "muito_proximo"
    else:
        detalhes["valor"] = "diferente"
    # diferenças maiores não somam pontos de valor

    # Data
    diff_dias = None
    if op.operacoes_data_lancamento:
        diff_dias = abs((op.operacoes_data_lancamento - ofx["data"]).days)
        detalhes["data_dias"] = diff_dias
        if diff_dias == 0:
            score += SCORE_DATA_EXATA
        elif diff_dias == 1:
            score += SCORE_DATA_1DIA
        elif diff_dias <= 3:
            score += SCORE_DATA_2A3DIAS
        elif diff_dias <= 7:
            score += SCORE_DATA_4A7DIAS

    # Tipo (C/D vs operacoes_tipo int)
    tipo_db = TIPO_INT_PARA_DC.get(op.operacoes_tipo, "D")
    tipo_compativel = tipo_db == ofx["tipo"]
    detalhes["tipo"] = "compativel" if tipo_compativel else "incompativel"
    if tipo_compativel:
        score += SCORE_TIPO_IGUAL
    else:
        score += PENALIDADE_TIPO

    # Similaridade de descrição
    ratio = _similaridade_memo(ofx.get("memo", ""), op.operacoes_descricao or "")
    detalhes["memo_ratio"] = round(ratio, 2)
    if ratio >= 0.85:
        score += SCORE_MEMO_ALTO
    elif ratio >= 0.70:
        score += SCORE_MEMO_MEDIO

    # Penalidades para divergências relevantes (evita "match forte" com valor/data distantes)
    if not valor_exato and not valor_muito_proximo:
        # valor plausível no pré-filtro, mas não suficientemente próximo
        if v_ofx > 0 and diff_valor / v_ofx > 0.02:
            score -= 25
            detalhes["regras"].append("Penalidade: valor não próximo (>2%)")
        elif diff_valor > 0.10:
            score -= 15
            detalhes["regras"].append("Penalidade: diferença de valor relevante")
    if diff_dias is not None and diff_dias > 3:
        score -= 10
        detalhes["regras"].append("Penalidade: data distante (>3 dias)")
    if diff_dias is not None and diff_dias > 7:
        score -= 10
        detalhes["regras"].append("Penalidade: data muito distante (>7 dias)")

    # Regras de consistência para pontuações altas
    if not tipo_compativel:
        score = min(score, THRESHOLD_FRACO - 1)
        detalhes["regras"].append("Limitado: tipo incompatível")
    if not (valor_exato or valor_muito_proximo):
        score = min(score, THRESHOLD_FORTE - 1)
        detalhes["regras"].append("Limitado: valor não suficientemente próximo")
    if diff_dias is not None and diff_dias > 3 and not valor_exato:
        score = min(score, THRESHOLD_FORTE - 1)
        detalhes["regras"].append("Limitado: data distante sem valor exato")

    score = max(score, 0)
    return score, detalhes


def _melhor_match(ofx: dict, candidatos: list[Operacao]) -> tuple[Optional[Operacao], int, dict]:
    melhor, melhor_score, melhor_detalhes = None, 0, {}
    for op in candidatos:
        s, detalhes = _calcular_score(ofx, op)
        if s > melhor_score:
            melhor_score, melhor, melhor_detalhes = s, op, detalhes
    return melhor, melhor_score, melhor_detalhes


def _classificar(score: int) -> str:
    if score >= THRESHOLD_DUP:
        return "DUPLICIDADE"
    if score >= THRESHOLD_FORTE:
        return "MATCH_FORTE"
    if score >= THRESHOLD_FRACO:
        return "MATCH_FRACO"
    return "NOVO"


def _serializar(op: Optional[Operacao]) -> Optional[dict]:
    if not op:
        return None
    tipo_dc = TIPO_INT_PARA_DC.get(op.operacoes_tipo, "D")
    return {
        "id":                  op.operacoes_id,
        "data_lancamento":     op.operacoes_data_lancamento.isoformat() if op.operacoes_data_lancamento else None,
        "valor":               float(op.operacoes_valor or 0),
        "tipo":                tipo_dc,
        "descricao":           op.operacoes_descricao,
        "ofx_fitid":           getattr(op, "operacoes_fitid", None),
        "ofx_memo":            op.operacoes_descricao,
        "operacoes_efetivado": op.operacoes_efetivado or 0,
    }