from datetime import date
from types import SimpleNamespace

from app.models import Operacao
from app.ofx_tools import (
    THRESHOLD_FORTE,
    THRESHOLD_FRACO,
    _calcular_score,
    _classificar,
    _melhor_match,
    _normalizar_texto_memo,
    _sugerir_transferencia,
)


def make_ofx(**kwargs):
    base = {
        "fitid": "FIT123",
        "tipo": "D",
        "valor": 100.00,
        "data": date(2026, 5, 10),
        "memo": "PAGAMENTO LOJA ABC",
    }
    base.update(kwargs)
    return base


def make_op(**kwargs):
    # instancia Operacao sem sessão DB
    op = Operacao()
    defaults = {
        "operacoes_id": 1,
        "operacoes_data_lancamento": date(2026, 5, 10),
        "operacoes_valor": -100.00,
        "operacoes_tipo": 3,  # despesa
        "operacoes_descricao": "PAGAMENTO LOJA ABC",
        "operacoes_fitid": None,
        "operacoes_conta": 10,
        "operacoes_validacao": 1,
        "operacoes_efetivado": 0,
    }
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(op, k, v)
    return op


class FakeQuery:
    def __init__(self, items):
        self.items = items

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.items)


class FakeDB:
    def __init__(self, operacoes=None, contas=None):
        # compat: FakeDB([ops]) => operacoes
        if contas is None and operacoes is not None and not isinstance(operacoes, dict):
            self.operacoes = operacoes
            self.contas = []
        elif isinstance(operacoes, dict):
            self.operacoes = operacoes.get("operacoes", [])
            self.contas = operacoes.get("contas", [])
        else:
            self.operacoes = operacoes or []
            self.contas = contas or []

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "ContaBancaria":
            return FakeQuery(self.contas)
        return FakeQuery(self.operacoes)


# --- Normalização de memo ---

def test_normalizar_texto_memo_remove_ruido_e_normaliza():
    assert _normalizar_texto_memo("  PAG*AMENTO   123-ABC!! ") == "pag amento 123 abc"


def test_normalizar_texto_memo_remove_ruido_basico():
    assert _normalizar_texto_memo("PAGTO-123 / LOJA@@") == "pagto 123 loja"


# --- Scoring / classificação ---

def test_match_forte_nao_ocorre_com_valor_muito_diferente_mesmo_com_data_tipo_memo():
    # valor OFX 100 vs DB 85 (~15% diff) => não deve ser forte
    ofx = make_ofx(valor=100.00, tipo="D", data=date(2026, 5, 10), memo="MERCADO CENTRAL")
    op = make_op(operacoes_valor=-85.00, operacoes_tipo=3, operacoes_data_lancamento=date(2026, 5, 10),
                 operacoes_descricao="MERCADO CENTRAL")
    score, detalhes = _calcular_score(ofx, op)
    assert _classificar(score) != "MATCH_FORTE"
    assert score < THRESHOLD_FORTE
    assert detalhes["valor"] == "diferente"


def test_match_forte_nao_ocorre_com_data_distante_mesmo_com_valor_tipo_memo():
    # 8 dias de diferença (dentro de janela plausível em outros contextos, mas score deve ser limitado)
    ofx = make_ofx(valor=100.00, tipo="D", data=date(2026, 5, 10), memo="ASSINATURA SERVICO")
    op = make_op(operacoes_valor=-100.00, operacoes_tipo=3, operacoes_data_lancamento=date(2026, 5, 18),
                 operacoes_descricao="ASSINATURA SERVICO")
    score, _ = _calcular_score(ofx, op)
    assert _classificar(score) != "MATCH_FORTE"


def test_match_forte_com_valor_exato_data_proxima_tipo_e_memo_similar():
    ofx = make_ofx(valor=100.00, tipo="D", data=date(2026, 5, 10), memo="PAGAMENTO LOJA ABC")
    op = make_op(operacoes_valor=-100.00, operacoes_tipo=3, operacoes_data_lancamento=date(2026, 5, 11),
                 operacoes_descricao="PAGAMENTO LOJA ABC")
    score, detalhes = _calcular_score(ofx, op)
    assert _classificar(score) == "MATCH_FORTE"
    assert score >= THRESHOLD_FORTE
    assert detalhes["valor"] == "exato"


def test_fitid_igual_retorna_duplicidade():
    ofx = make_ofx(fitid="ABC123")
    op = make_op(operacoes_fitid="ABC123")
    score, detalhes = _calcular_score(ofx, op)
    assert score == 100
    assert _classificar(score) == "DUPLICIDADE"
    assert detalhes["fitid"] == "igual"


def test_operacao_vinculada_a_outro_fitid_nao_da_match():
    ofx = make_ofx(fitid="NOVO123")
    op = make_op(operacoes_fitid="ANTIGO999", operacoes_valor=-100.00, operacoes_tipo=3,
                 operacoes_data_lancamento=date(2026, 5, 10), operacoes_descricao="PAGAMENTO LOJA ABC")
    score, detalhes = _calcular_score(ofx, op)
    assert score == 0
    assert detalhes["fitid"] == "vinculado_outro"


def test_tipo_incompativel_limita_classificacao():
    # OFX crédito vs operação despesa (tipo 3 -> D)
    ofx = make_ofx(tipo="C", valor=100.00)
    op = make_op(operacoes_valor=100.00, operacoes_tipo=3)
    score, detalhes = _calcular_score(ofx, op)
    assert score < THRESHOLD_FRACO
    assert detalhes["tipo"] == "incompativel"


def test_melhor_match_escolhe_maior_score():
    ofx = make_ofx(valor=50.00, tipo="D", data=date(2026, 5, 10), memo="CAFE PADARIA")
    op_ruim = make_op(operacoes_id=1, operacoes_valor=-50.00, operacoes_tipo=3,
                      operacoes_data_lancamento=date(2026, 5, 18), operacoes_descricao="COMPRA DIVERSA")
    op_bom = make_op(operacoes_id=2, operacoes_valor=-50.00, operacoes_tipo=3,
                     operacoes_data_lancamento=date(2026, 5, 10), operacoes_descricao="CAFE PADARIA")
    melhor, score, detalhes = _melhor_match(ofx, [op_ruim, op_bom])
    assert melhor.operacoes_id == 2
    assert score >= THRESHOLD_FORTE
    assert isinstance(detalhes, dict)


# --- Sugestão de transferência ---

def test_sugere_transferencia_quando_ha_contraparte_compativel_em_outra_conta():
    ofx = make_ofx(tipo="D", valor=200.00, data=date(2026, 5, 10), memo="TRANSFERENCIA PIX")
    contraparte = make_op(
        operacoes_id=99,
        operacoes_conta=20,
        operacoes_valor=200.00,  # entrada na outra conta
        operacoes_tipo=1,
        operacoes_data_lancamento=date(2026, 5, 10),
        operacoes_descricao="TRANSFERENCIA PIX RECEBIDA",
    )
    db = FakeDB([contraparte])
    sugestao = _sugerir_transferencia(ofx, db, conta_id=10, melhor_match=None)
    assert sugestao is not None
    assert sugestao["conta_destino"] == 20
    assert sugestao["operacao_contraparte_id"] == 99
    assert sugestao["score"] >= 70


def test_nao_sugere_transferencia_sem_contraparte_plausivel():
    ofx = make_ofx(tipo="D", valor=200.00, data=date(2026, 5, 10), memo="COMPRA LOJA")
    contraparte_distante = make_op(
        operacoes_id=77,
        operacoes_conta=20,
        operacoes_valor=150.00,  # valor diferente
        operacoes_tipo=1,
        operacoes_data_lancamento=date(2026, 5, 20),  # data distante
        operacoes_descricao="OUTRO LANCAMENTO",
    )
    db = FakeDB([contraparte_distante])
    sugestao = _sugerir_transferencia(ofx, db, conta_id=10, melhor_match=None)
    assert sugestao is None


def make_conta(conta_id, nome, tipo_conta=1):
    return SimpleNamespace(conta_id=conta_id, nome_conta=nome, tipo_conta=tipo_conta)


def test_sugere_transferencia_por_nome_da_conta_no_memo_mesmo_sem_contraparte():
    ofx = make_ofx(tipo="C", valor=350.00, data=date(2026, 5, 10), memo="Transferência recebida pelo Pix - Heracles R")
    conta_origem = make_conta(10, "Conta Corrente", 1)
    conta_heracles = make_conta(20, "Heracles", 1)
    conta_cartao = make_conta(30, "Cartão Crédito", 4)
    db = FakeDB(operacoes=[], contas=[conta_origem, conta_heracles, conta_cartao])
    sugestao = _sugerir_transferencia(ofx, db, conta_id=10, melhor_match=None)
    assert sugestao is not None
    assert sugestao["conta_destino"] == 20
    assert sugestao["operacao_contraparte_id"] is None
    assert "Heracles" in (sugestao.get("motivo") or "")


def test_nao_sugere_transferencia_para_conta_cartao_credito_quando_memo_indica_pix():
    ofx = make_ofx(tipo="C", valor=350.00, data=date(2026, 5, 10), memo="Transferência recebida pelo Pix - Fulano")
    conta_origem = make_conta(10, "Conta Corrente", 1)
    conta_cartao = make_conta(30, "Fulano", 4)
    db = FakeDB(operacoes=[], contas=[conta_origem, conta_cartao])
    sugestao = _sugerir_transferencia(ofx, db, conta_id=10, melhor_match=None)
    assert sugestao is None


def test_sugestao_por_contraparte_ignora_conta_cartao_e_pode_usar_nome_no_memo():
    ofx = make_ofx(tipo="C", valor=350.00, data=date(2026, 5, 10), memo="Transferência recebida pelo Pix - Heracles R")
    conta_origem = make_conta(10, "Conta Corrente", 1)
    conta_heracles = make_conta(20, "Heracles", 1)
    conta_cartao = make_conta(30, "Cartão", 4)
    contraparte_cartao = make_op(operacoes_id=50, operacoes_conta=30, operacoes_valor=-350.00, operacoes_tipo=4,
                                 operacoes_data_lancamento=date(2026, 5, 10), operacoes_descricao="PAGTO CARTAO")
    contraparte_heracles = make_op(operacoes_id=51, operacoes_conta=20, operacoes_valor=-350.00, operacoes_tipo=4,
                                   operacoes_data_lancamento=date(2026, 5, 10), operacoes_descricao="PIX ENVIADO")
    db = FakeDB(operacoes=[contraparte_cartao, contraparte_heracles], contas=[conta_origem, conta_heracles, conta_cartao])
    sugestao = _sugerir_transferencia(ofx, db, conta_id=10, melhor_match=None)
    assert sugestao is not None
    assert sugestao["conta_destino"] == 20
    assert sugestao["operacao_contraparte_id"] in (None, 51)
