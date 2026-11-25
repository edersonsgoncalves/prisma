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
import uuid

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
    tipo: str               # "C" crédito | "D" débito (tipo do OFX)
    memo: Optional[str] = None
    match_id: Optional[int] = None   # operacoes_id do lançamento existente
    conta_id: Optional[int] = None   # conta_id de contas_bancarias (conta do extrato)
    conta_destino: Optional[int] = None  # contraparte para transferência
    tipo_operacao: Optional[int] = None  # 1=Receita, 3=Despesa, 4=Transferência (para inserir)
    como_efetivado: bool = True      # status de conciliação


# ═══════════════════════════════════════════════════════════════════════════
# FUNÇÕES PRINCIPAIS
# ═══════════════════════════════════════════════════════════════════════════

def _sugerir_conta_do_arquivo(conta_detectada: dict, contas: list[ContaBancaria]) -> Optional[dict]:
    """Sugere conta bancária com base nos dados detectados no OFX."""
    if not conta_detectada or not contas:
        return None
    acct_id = "".join(ch for ch in str(conta_detectada.get("acct_id", "")) if ch.isalnum())
    bank_id = "".join(ch for ch in str(conta_detectada.get("bank_id", "")) if ch.isalnum())
    if not acct_id and not bank_id:
        return None

    def norm(s: str) -> str:
        return "".join(ch for ch in (s or "").lower() if ch.isalnum())

    melhor = None  # (score, conta, motivo)
    for c in contas:
        nome = norm(getattr(c, "nome_conta", "") or "")
        score = 0
        motivos = []
        if acct_id and nome:
            if norm(acct_id) and norm(acct_id) in nome:
                score += 100
                motivos.append(f"número da conta do arquivo ({conta_detectada.get('acct_id')}) encontrado no nome da conta")
            else:
                # sufixo: últimos 4-8 dígitos
                dig = "".join(ch for ch in acct_id if ch.isdigit())
                for n in (8, 6, 5, 4):
                    if len(dig) >= n and dig[-n:] in nome:
                        score += 60 + n
                        motivos.append(f"final da conta ({dig[-n:]}) encontrado no nome da conta")
                        break
        if bank_id and bank_id in nome:
            score += 20
            motivos.append(f"código do banco ({bank_id}) encontrado no nome da conta")
        if score > 0 and (melhor is None or score > melhor[0]):
            melhor = (score, c, "; ".join(motivos))

    if not melhor:
        return None
    _, conta, motivo = melhor
    return {
        "conta_id": conta.conta_id,
        "nome_conta": conta.nome_conta,
        "motivo": motivo,
    }


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
    conta_detectada = {}
    try:
        conta_detectada = reader.parse_account(filepath) or {}
    except Exception:
        conta_detectada = {}

    contas_todas = db.query(ContaBancaria).all()
    conta_sugerida = _sugerir_conta_do_arquivo(conta_detectada, contas_todas)
    conta_sugerida_automatica = bool((not conta_id) and conta_sugerida)
    conta_efetiva = conta_id or (conta_sugerida["conta_id"] if conta_sugerida else None)

    resultado = []
    for t in transacoes_raw:
        ofx = _normalizar(t)
        candidatos = _buscar_candidatos(ofx, db, conta_efetiva)
        melhor, score, detalhes = _melhor_match(ofx, candidatos)
        classif = _classificar(score)
        sugestao_transferencia = _sugerir_transferencia(ofx, db, conta_efetiva, melhor)

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
            "sugestaoTransferencia": sugestao_transferencia,
        })

    return {
        "transacoes": resultado,
        "conta_detectada": conta_detectada,
        "conta_sugerida": conta_sugerida,
        "conta_sugerida_automatica": conta_sugerida_automatica,
        "conta_id_usada": conta_efetiva,
    }


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
    # Tipo de operação para inserção: 1=Receita, 3=Despesa, 4=Transferência
    tipo_int = payload.tipo_operacao or (1 if payload.tipo == "C" else 3)

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
        # para transferências preserva o sinal do lançamento conciliado (lado do par)
        if op.operacoes_tipo == 4:
            sinal = -1 if float(op.operacoes_valor or 0) < 0 else 1
            op.operacoes_valor = abs(payload.valor) * sinal
        else:
            op.operacoes_valor = -abs(payload.valor) if payload.tipo == "D" else abs(payload.valor)

        # Concatenar a descrição do OFX (até 20 caracteres)
        memo_str = (payload.memo or "").strip()[:20]
        if memo_str and f"({memo_str})" not in (op.operacoes_descricao or ""):
            nova_desc = f"{op.operacoes_descricao or ''} ({memo_str})".strip()
            op.operacoes_descricao = nova_desc[:255]

        op.operacoes_efetivado = 1 if payload.como_efetivado else 0
        if payload.como_efetivado:
            op.operacoes_data_efetivado = datetime.combine(data_lancamento, datetime.min.time())
        else:
            op.operacoes_data_efetivado = None
        op.operacoes_fitid = payload.fitid   # requer migration (ver docstring)
        db.commit()
        return {"status": "efetivado", "id": op.operacoes_id}

    # ── INSERIR ───────────────────────────────────────────────────────────
    if payload.acao == "inserir":
        if not payload.conta_id:
            raise ValueError("Selecione e confirme a conta bancária do extrato antes de inserir lançamentos.")
        conta = db.query(ContaBancaria).filter(
            ContaBancaria.conta_id == payload.conta_id
        ).first()
        if not conta:
            raise ValueError("Conta não encontrada.")

        descricao = (payload.memo or "Importado via OFX").strip() or "Importado via OFX"
        dt_efet = datetime.combine(data_lancamento, datetime.min.time()) if payload.como_efetivado else None

        # Transferência: cria par de operações vinculadas
        if tipo_int == 4:
            if not payload.conta_destino:
                raise ValueError("Selecione a conta de destino para inserir como transferência.")
            if int(payload.conta_destino) == int(conta.conta_id):
                raise ValueError("Conta de destino deve ser diferente da conta de origem.")

            conta_destino = db.query(ContaBancaria).filter(
                ContaBancaria.conta_id == payload.conta_destino
            ).first()
            if not conta_destino:
                raise ValueError("Conta de destino não encontrada.")

            grupo_id = f"TRF-OFX-{uuid.uuid4().hex[:8]}"
            # OFX D: saída da conta do extrato -> destino; OFX C: entrada na conta do extrato <- origem
            if payload.tipo == "D":
                conta_saida_id = conta.conta_id
                conta_entrada_id = conta_destino.conta_id
            else:
                conta_saida_id = conta_destino.conta_id
                conta_entrada_id = conta.conta_id

            op_saida = Operacao(
                operacoes_data_lancamento=data_lancamento,
                operacoes_descricao=descricao,
                operacoes_conta=conta_saida_id,
                operacoes_valor=-abs(payload.valor),
                operacoes_tipo=4,
                operacoes_efetivado=1 if payload.como_efetivado else 0,
                operacoes_data_efetivado=dt_efet,
                operacoes_fitid=payload.fitid,
                operacoes_validacao=1,
                operacoes_grupo_id=grupo_id,
            )
            op_entrada = Operacao(
                operacoes_data_lancamento=data_lancamento,
                operacoes_descricao=descricao,
                operacoes_conta=conta_entrada_id,
                operacoes_valor=abs(payload.valor),
                operacoes_tipo=4,
                operacoes_efetivado=1 if payload.como_efetivado else 0,
                operacoes_data_efetivado=dt_efet,
                operacoes_validacao=1,
                operacoes_grupo_id=grupo_id,
            )
            db.add(op_saida)
            db.add(op_entrada)
            db.flush()
            op_saida.operacoes_transf_rel = op_entrada.operacoes_id
            op_entrada.operacoes_transf_rel = op_saida.operacoes_id
            db.commit()
            return {"status": "inserido", "id": op_saida.operacoes_id, "id_rel": op_entrada.operacoes_id}

        valor_op = -abs(payload.valor) if tipo_int == 3 else abs(payload.valor)
        nova = Operacao(
            operacoes_data_lancamento=data_lancamento,
            operacoes_descricao=descricao,
            operacoes_conta=conta.conta_id,
            operacoes_valor=valor_op,
            operacoes_tipo=tipo_int,
            operacoes_efetivado=1 if payload.como_efetivado else 0,
            operacoes_data_efetivado=dt_efet,
            operacoes_fitid=payload.fitid,
            operacoes_validacao=1,
        )
        db.add(nova)
        db.commit()
        db.refresh(nova)
        return {"status": "inserido", "id": nova.operacoes_id}

    raise ValueError(f"Ação desconhecida: {payload.acao}")


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS INTERNOS
# ═══════════════════════════════════════════════════════════════════════════


def _conta_elegivel_para_transferencia(conta: ContaBancaria, memo: str) -> bool:
    """Define se a conta pode ser sugerida como contraparte de transferência bancária/PIX."""
    # Evita sugerir cartão de crédito como contraparte de transferência comum (ex.: Pix/TED/DOC)
    if int(getattr(conta, "tipo_conta", 0) or 0) == 4:
        return False
    return True


def _extrair_nome_conta_do_memo(memo: str, conta: ContaBancaria) -> tuple[int, Optional[str]]:
    """Retorna (score, motivo) se o nome da conta aparece como pista no texto do lançamento."""
    memo_norm = _normalizar_texto_memo(memo)
    nome_norm = _normalizar_texto_memo(getattr(conta, "nome_conta", "") or "")
    if not memo_norm or not nome_norm:
        return 0, None

    # ignora nomes muito curtos/genéricos para evitar falsos positivos
    if len(nome_norm) < 3:
        return 0, None

    memo_tokens = f" {memo_norm} "
    nome_tokens = f" {nome_norm} "
    motivo = None
    score = 0

    # match exato do nome da conta dentro do memo (tokens/frase)
    if nome_tokens in memo_tokens:
        score = 100
        motivo = f"nome da conta identificado no lançamento ({conta.nome_conta})"
    else:
        # fallback: todas as palavras relevantes do nome presentes no memo
        partes = [p for p in nome_norm.split() if len(p) >= 3]
        if len(partes) >= 2 and all(f" {p} " in memo_tokens for p in partes):
            score = 85
            motivo = f"nome da conta identificado no lançamento ({conta.nome_conta})"

    # reforço quando o memo indica transferência/pix/ted/doc
    if score > 0:
        termos_transfer = ("pix", "transfer", "ted", "doc", "transf")
        if any(t in memo_norm for t in termos_transfer):
            score += 10
    return score, motivo


def _sugerir_transferencia(ofx: dict, db: Session, conta_id: Optional[int], melhor_match: Optional[Operacao]) -> Optional[dict]:
    """Sugere classificação como transferência quando há contraparte plausível em outra conta."""
    # Se já há match forte de duplicidade/concorrência exata na mesma conta, evita sugerir transferência
    if melhor_match and getattr(melhor_match, "operacoes_fitid", None) == ofx.get("fitid"):
        return None

    v_ofx = abs(float(ofx.get("valor") or 0))
    if v_ofx <= 0:
        return None

    memo = ofx.get("memo", "") or ""
    memo_norm = _normalizar_texto_memo(memo)
    indica_transfer = any(t in memo_norm for t in ("pix", "transfer", "ted", "doc", "transf"))

    contas_raw = db.query(ContaBancaria).all()
    contas = [c for c in contas_raw if hasattr(c, "conta_id") and hasattr(c, "nome_conta")]
    contas_por_id = {c.conta_id: c for c in contas}

    # Contas candidatas por elegibilidade/contexto
    contas_candidatas = []
    for c in contas:
        if conta_id and c.conta_id == conta_id:
            continue
        if not _conta_elegivel_para_transferencia(c, memo):
            continue
        contas_candidatas.append(c)
    contas_candidatas_ids = {c.conta_id for c in contas_candidatas}

    melhor_sugestao = None  # (score, conta_destino, operacao_contraparte_id, motivo)

    # 1) Pista forte pelo nome da conta no texto do lançamento (mesmo sem contraparte existente)
    for c in contas_candidatas:
        score_nome, motivo_nome = _extrair_nome_conta_do_memo(memo, c)
        if not motivo_nome:
            continue
        # nome identificado é um sinal forte; transferência explícita reforça
        score = score_nome
        if indica_transfer:
            score = max(score, 110)
        cand = (score, c.conta_id, None, motivo_nome)
        if not melhor_sugestao or cand[0] > melhor_sugestao[0]:
            melhor_sugestao = cand

    # Se a conta do extrato for cartão e o memo indica pix/transfer bancária, não forçar sugestão de cartão
    # (já filtrado por elegibilidade; manter regra explícita para clareza)

    # 2) Contraparte operacional existente em outra conta (valor/data/sinal)
    data_ref = ofx["data"]
    data_min = data_ref - timedelta(days=3)
    data_max = data_ref + timedelta(days=3)

    # OFX D (saída da conta importada) => provável entrada (+) em outra conta
    # OFX C (entrada na conta importada) => provável saída (-) em outra conta
    if ofx["tipo"] == "D":
        cond_valor = Operacao.operacoes_valor > 0
    else:
        cond_valor = Operacao.operacoes_valor < 0

    q = db.query(Operacao).filter(
        Operacao.operacoes_data_lancamento >= data_min,
        Operacao.operacoes_data_lancamento <= data_max,
        Operacao.operacoes_validacao == 1,
        cond_valor,
    )
    if conta_id:
        q = q.filter(Operacao.operacoes_conta != conta_id)

    # Se temos informações de contas, restringe a contrapartes elegíveis (ex.: exclui cartão de crédito)
    if contas:
        if contas_candidatas_ids:
            q = q.filter(Operacao.operacoes_conta.in_(list(contas_candidatas_ids)))
        else:
            candidatos = []
            q = None
    candidatos = q.all() if q is not None else []

    for op in candidatos:
        conta_op = contas_por_id.get(op.operacoes_conta)
        if contas and (not conta_op or not _conta_elegivel_para_transferencia(conta_op, memo)):
            continue

        v_db = abs(float(op.operacoes_valor or 0))
        diff_valor = abs(v_db - v_ofx)
        if diff_valor > 0.05:
            continue
        diff_dias = abs((op.operacoes_data_lancamento - data_ref).days) if op.operacoes_data_lancamento else 99
        score = 0
        motivo_partes = []
        if diff_valor < 0.01:
            score += 60
            motivo_partes.append("valor igual")
        else:
            score += 45
            motivo_partes.append("valor muito próximo")
        if diff_dias == 0:
            score += 30
            motivo_partes.append("mesma data")
        elif diff_dias == 1:
            score += 20
            motivo_partes.append("1 dia de diferença")
        elif diff_dias <= 3:
            score += 10
            motivo_partes.append(f"{diff_dias} dias de diferença")
        # bônus se já for transferência ou descrição semelhante
        if int(op.operacoes_tipo or 0) == 4:
            score += 10
            motivo_partes.append("contraparte já é transferência")
        ratio = _similaridade_memo(memo, op.operacoes_descricao or "")
        if ratio >= 0.7:
            score += 10
            motivo_partes.append("descrição semelhante")

        # reforço por pista de nome da conta no memo (ex.: "Heracles")
        score_nome, motivo_nome = _extrair_nome_conta_do_memo(memo, conta_op)
        if motivo_nome:
            score += 40
            motivo_partes.append(motivo_nome)

        if indica_transfer and score_nome > 0:
            score += 10

        cand = (score, op.operacoes_conta, op.operacoes_id, ", ".join(motivo_partes) or "contraparte compatível encontrada em outra conta")
        if not melhor_sugestao or cand[0] > melhor_sugestao[0]:
            melhor_sugestao = cand

    if not melhor_sugestao:
        return None

    score, conta_destino, op_contraparte_id, motivo = melhor_sugestao

    # limiar: nome identificado no memo pode sugerir mesmo sem contraparte operacional
    limiar = 70
    if op_contraparte_id is None:
        limiar = 90 if indica_transfer or "nome da conta identificado" in (motivo or "") else 100
    if score < limiar:
        return None

    return {
        "conta_destino": conta_destino,
        "operacao_contraparte_id": op_contraparte_id,
        "motivo": motivo or "contraparte compatível encontrada em outra conta",
        "score": score,
    }

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


def _normalizar_texto_memo(texto: str) -> str:
    import re
    t = (texto or "").lower()
    t = re.sub(r"[^a-z0-9áàâãéêíóôõúç\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _similaridade_memo(memo_ofx: str, memo_db: str) -> float:
    a = _normalizar_texto_memo(memo_ofx)
    b = _normalizar_texto_memo(memo_db)
    if not a or not b:
        return 0.0
    # usa a melhor entre comparação direta e por tokens únicos ordenados
    ratio_direto = SequenceMatcher(None, a, b).ratio()
    ta = " ".join(sorted(set(a.split())))
    tb = " ".join(sorted(set(b.split())))
    ratio_tokens = SequenceMatcher(None, ta, tb).ratio() if ta and tb else 0.0
    return max(ratio_direto, ratio_tokens)


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