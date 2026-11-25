import sys
from pathlib import Path

# Adiciona o diretório raiz ao sys.path para importar app
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.database import engine
from sqlalchemy import text


def _add_column(conn, ddl: str, name: str):
    print(f"Verificando/Adicionando coluna {name}...")
    try:
        conn.execute(text(ddl))
        conn.commit()
        print(f"Coluna {name} adicionada com sucesso.")
    except Exception as e:
        if "Duplicate column name" in str(e):
            print(f"Coluna {name} já existe.")
        else:
            raise


def migrate():
    print("Iniciando migração contas_codigo_banco/contas_numero...")
    with engine.connect() as conn:
        _add_column(
            conn,
            "ALTER TABLE contas_bancarias ADD COLUMN contas_codigo_banco VARCHAR(20) NULL AFTER contas_desconsiderar_saldo",
            "contas_codigo_banco",
        )
        _add_column(
            conn,
            "ALTER TABLE contas_bancarias ADD COLUMN contas_numero VARCHAR(60) NULL AFTER contas_codigo_banco",
            "contas_numero",
        )
    print("Migração concluída com sucesso!")


if __name__ == "__main__":
    migrate()
