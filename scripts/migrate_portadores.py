import sys
import os
from pathlib import Path

# Adiciona o diretório raiz ao sys.path para importar app
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.database import engine
from sqlalchemy import text

def migrate():
    print("Iniciando migração de portadores...")
    
    with engine.connect() as conn:
        # 1. Cria a tabela cartoes_adicionais
        print("Criando tabela cartoes_adicionais...")
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS cartoes_adicionais (
                    adicional_id INT AUTO_INCREMENT PRIMARY KEY,
                    conta_id INT NOT NULL,
                    adicional_nome VARCHAR(100) NOT NULL,
                    cartao_final VARCHAR(10) NOT NULL,
                    apelido VARCHAR(50) NULL,
                    conta_vinculada INT NULL,
                    titular SMALLINT DEFAULT 0,
                    ativo SMALLINT DEFAULT 1,
                    CONSTRAINT fk_adicional_conta_id FOREIGN KEY (conta_id) REFERENCES contas_bancarias(conta_id),
                    CONSTRAINT fk_adicional_conta_vinculada FOREIGN KEY (conta_vinculada) REFERENCES contas_bancarias(conta_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """))
            conn.commit()
            print("Tabela cartoes_adicionais verificada/criada.")
        except Exception as e:
            print(f"Erro ao criar tabela: {e}")
            return

        # 2. Adiciona a coluna operacoes_adicional_id na tabela operacoes
        print("Adicionando coluna operacoes_adicional_id em operacoes...")
        try:
            conn.execute(text("""
                ALTER TABLE operacoes 
                ADD COLUMN operacoes_adicional_id INT NULL AFTER operacoes_grupo_id,
                ADD CONSTRAINT fk_operacoes_adicional FOREIGN KEY (operacoes_adicional_id) REFERENCES cartoes_adicionais(adicional_id)
            """))
            conn.commit()
            print("Coluna e constraint adicionadas com sucesso.")
        except Exception as e:
            if "Duplicate column name" in str(e):
                print("Coluna já existe.")
            else:
                print(f"Erro ao adicionar coluna: {e}")
                return

    print("Migração de portadores concluída com sucesso!")

if __name__ == "__main__":
    migrate()
