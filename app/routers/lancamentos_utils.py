from datetime import date
from calendar import monthrange
from decimal import Decimal
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import Operacao, ContaBancaria, FaturaCartao, LogOperacao

def log_evento(db: Session, acao: str, entidade: str, entidade_id: int, detalhes: str, usuario_id: int = None):
    log = LogOperacao(
        log_usuario_id=usuario_id,
        log_acao=acao,
        log_entidade=entidade,
        log_entidade_id=entidade_id,
        log_detalhes=detalhes
    )
    db.add(log)

def get_or_create_fatura(db: Session, cartao_id: int, data_original: date) -> int:
    """
    Localiza a fatura aberta para o cartão e data informados.
    Se não encontrar, cria uma nova seguindo o dia de fechamento do cartão.
    """
    cartao = db.query(ContaBancaria).filter(ContaBancaria.conta_id == cartao_id).first()
    if not cartao:
        return None
        
    dia_fechamento = cartao.contas_cartao_fechamento or 1
    dia_vencimento = cartao.contas_prev_debito or 10

    if data_original.day >= dia_fechamento:
        mes_ref = data_original.month % 12 + 1
        ano_ref = data_original.year + (1 if data_original.month == 12 else 0)
    else:
        mes_ref = data_original.month
        ano_ref = data_original.year

    data_ref = date(ano_ref, mes_ref, 1)

    fatura = db.query(FaturaCartao).filter(
        FaturaCartao.conta_id == cartao_id,
        FaturaCartao.mes_referencia == data_ref,
        FaturaCartao.fechado == 0
    ).first()

    if fatura:
        return fatura.fatura_id

    ultimo_dia_mes = monthrange(ano_ref, mes_ref)[1]
    dia_vencimento_ajustado = max(1, min(dia_vencimento, ultimo_dia_mes))
    dia_fechamento_ajustado = max(1, min(dia_fechamento, ultimo_dia_mes))

    vencimento = date(ano_ref, mes_ref, dia_vencimento_ajustado)
    fechamento = date(ano_ref, mes_ref, dia_fechamento_ajustado)
    
    nova_fatura = FaturaCartao(
        conta_id=cartao_id,
        data_vencimento=vencimento,
        data_fechamento=fechamento,
        mes_referencia=data_ref,
        fechado=0,
        valor_total=0
    )
    db.add(nova_fatura)
    db.flush()
    
    log_evento(db, "SYSTEM", "FATURA", nova_fatura.fatura_id, 
               f"Fatura criada automaticamente para {data_ref.strftime('%m/%Y')} (Cartão ID {cartao_id})")
    
    return nova_fatura.fatura_id

def recalcular_total_fatura(db: Session, fatura_id: int):
    fatura = db.query(FaturaCartao).filter(FaturaCartao.fatura_id == fatura_id).first()
    if not fatura:
        return

    total = db.query(func.sum(Operacao.operacoes_valor)).filter(
        Operacao.operacoes_fatura == fatura_id,
        Operacao.operacoes_tipo != 0,
        Operacao.operacoes_validacao == 1,
    ).scalar() or Decimal("0.00")

    fatura.valor_total = total
    db.commit()
#