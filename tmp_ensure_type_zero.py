
from app.database import SessionLocal
from app.models import TipoOperacao

def ensure_type_zero():
    db = SessionLocal()
    try:
        type_zero = db.query(TipoOperacao).filter(TipoOperacao.tipo_operacao_id == 0).first()
        if not type_zero:
            print("Criando TipoOperacao 0 (Pagamento Fatura)...")
            new_type = TipoOperacao(tipo_operacao_id=0, tipo_operacao_nome="Pagamento Fatura")
            db.add(new_type)
            db.commit()
            print("Tipo 0 criado com sucesso.")
        else:
            print("Tipo 0 já existe.")
    except Exception as e:
        print(f"Erro ao verificar/criar Tipo 0: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    ensure_type_zero()
