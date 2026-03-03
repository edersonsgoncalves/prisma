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
SCORE_VALOR_EXATO = 40
SCORE_DATA_EXATA  = 30
SCORE_DATA_1DIA   = 20
SCORE_DATA_3DIAS  = 10
SCORE_MEMO_80     = 20
SCORE_TIPO_IGUAL  = 10

THRESHOLD_DUP    = 100
THRESHOLD_FORTE  = 70
THRESHOLD_FRACO  = 40

JANELA_DIAS = 10

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
        melhor, score = _melhor_match(ofx, candidatos)
        classif = _classificar(score)

        resultado.append({
            "fitid":         ofx["fitid"],
            "data":          ofx["data"].isoformat(),
            "valor":         float(ofx["valor"]),
            "tipo":          ofx["tipo"],
            "memo":          ofx["memo"],
            "score":         score,
            "classificacao": classif,
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

        nova = Operacao(
            operacoes_data_lancamento = data_lancamento,
            operacoes_descricao       = payload.memo or "Importado via OFX",
            operacoes_conta           = conta.conta_id,
            operacoes_valor           = abs(payload.valor),
            operacoes_tipo            = tipo_int,
            operacoes_efetivado       = 1 if payload.como_efetivado else 0,
            operacoes_data_efetivado  = datetime.combine(data_lancamento, datetime.min.time()) if payload.como_efetivado else None,
            operacoes_fitid           = payload.fitid,   # requer migration
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

def _normalizar(t: dict) -> dict:
    """Padroniza o dict vindo do OFXReader para campos internos."""
    valor_raw = float(t.get("valor") or t.get("TRNAMT") or 0)
    tipo  = "C" if valor_raw >= 0 else "D"
    valor = abs(valor_raw)

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
    """Busca Operacoes com mesmo valor dentro da janela de datas."""
    data_min = ofx["data"] - timedelta(days=JANELA_DIAS)
    data_max = ofx["data"] + timedelta(days=JANELA_DIAS)

    q = db.query(Operacao).filter(
        Operacao.operacoes_valor           == round(ofx["valor"], 2),
        Operacao.operacoes_data_lancamento >= data_min,
        Operacao.operacoes_data_lancamento <= data_max,
    )
    if conta_id:
        q = q.filter(Operacao.operacoes_conta == conta_id)

    return q.all()


def _calcular_score(ofx: dict, op: Operacao) -> int:
    score = 0

    # FITID idêntico → duplicidade certa
    fitid_db = getattr(op, "operacoes_fitid", None)
    if ofx["fitid"] and fitid_db and fitid_db == ofx["fitid"]:
        return SCORE_FITID_MATCH

    # Valor
    if abs(float(op.operacoes_valor or 0) - ofx["valor"]) < 0.01:
        score += SCORE_VALOR_EXATO

    # Data
    if op.operacoes_data_lancamento:
        diff = abs((op.operacoes_data_lancamento - ofx["data"]).days)
        if diff == 0:   score += SCORE_DATA_EXATA
        elif diff == 1: score += SCORE_DATA_1DIA
        elif diff <= 3: score += SCORE_DATA_3DIAS

    # Tipo (C/D vs operacoes_tipo int)
    tipo_db = TIPO_INT_PARA_DC.get(op.operacoes_tipo, "D")
    if tipo_db == ofx["tipo"]:
        score += SCORE_TIPO_IGUAL

    # Similaridade de descrição
    memo_ofx = ofx["memo"].lower()
    memo_db  = (op.operacoes_descricao or "").lower()
    if memo_ofx and memo_db:
        ratio = SequenceMatcher(None, memo_ofx, memo_db).ratio()
        if ratio >= 0.80:
            score += SCORE_MEMO_80

    return score


def _melhor_match(ofx: dict, candidatos: list[Operacao]) -> tuple[Optional[Operacao], int]:
    melhor, melhor_score = None, 0
    for op in candidatos:
        s = _calcular_score(ofx, op)
        if s > melhor_score:
            melhor_score, melhor = s, op
    return melhor, melhor_score


def _classificar(score: int) -> str:
    if score >= THRESHOLD_DUP:    return "DUPLICIDADE"
    if score >= THRESHOLD_FORTE:  return "MATCH_FORTE"
    if score >= THRESHOLD_FRACO:  return "MATCH_FRACO"
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