"""
Cria as tabelas do módulo de investimentos (invest_*).

Uso:
  python scripts/migrate_investimentos.py
"""
from pathlib import Path
import sys

# garante import do pacote app ao executar a partir da raiz
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import engine, Base  # noqa: E402
import app.investimentos.models  # noqa: F401,E402  (registra tabelas no metadata)

INVEST_TABLES = [t for name, t in Base.metadata.tables.items() if name.startswith("invest_")]


def main():
    if not INVEST_TABLES:
        print("Nenhuma tabela invest_* encontrada no metadata.")
        return
    print(f"Criando/verificando {len(INVEST_TABLES)} tabela(s) invest_*...")
    Base.metadata.create_all(bind=engine, tables=INVEST_TABLES)
    for t in INVEST_TABLES:
        print(f" - ok: {t.name}")
    print("Concluído.")


if __name__ == "__main__":
    main()
