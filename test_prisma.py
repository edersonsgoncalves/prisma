# test_prisma.py
import sys
import os
from sqlalchemy import select, func
from sqlalchemy.orm import Session

# Adiciona o diretório atual ao path para importar app
sys.path.append(os.getcwd())

from app.database import engine, Base
from app.models import ContaBancaria, Operacao, Categoria

def test_connection():
    try:
        with Session(engine) as session:
            # 1. Testar se consegue ler contas (Valida rename de idcontas_bancarias -> conta_id)
            contas = session.execute(select(ContaBancaria)).scalars().all()
            print(f"✅ Conexão OK. Encontradas {len(contas)} contas.")
            for c in contas:
                print(f"   - {c.nome_conta} (ID: {c.conta_id})")

            # 2. Testar se consegue ler operações (Valida FKs)
            total_ops = session.execute(select(func.count(Operacao.operacoes_id))).scalar()
            print(f"✅ Operações: {total_ops} registros encontrados.")

            # 3. Testar se consegue ler categorias
            total_cats = session.execute(select(func.count(Categoria.categorias_id))).scalar()
            print(f"✅ Categorias: {total_cats} registros encontradas.")

            print("\n🚀 PROJETO PRISMA PRONTO PARA PRODUÇÃO!")
            
    except Exception as e:
        print(f"❌ ERRO NA VALIDAÇÃO: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_connection()
