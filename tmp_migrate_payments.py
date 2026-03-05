
from app.database import SessionLocal
from app.models import Operacao

def migrate_payments():
    db = SessionLocal()
    try:
        # Busca operações que são transferências (4), positivas e vinculadas a uma fatura
        # Geralmente essas são os pagamentos de fatura criados pelo sistema.
        ops = db.query(Operacao).filter(
            Operacao.operacoes_tipo == 4,
            Operacao.operacoes_valor > 0,
            Operacao.operacoes_fatura.isnot(None)
        ).all()
        
        count = 0
        for op in ops:
            # Verifica a descrição para ter mais certeza (opcional, mas seguro)
            if "Pagamento" in (op.operacoes_descricao or ""):
                op.operacoes_tipo = 0
                count += 1
        
        db.commit()
        print(f"Migrados {count} lançamentos de pagamento para o Tipo 0.")
    except Exception as e:
        print(f"Erro na migração: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate_payments()
