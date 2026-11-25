"""Popula indexadores padrão para investimentos."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.investimentos.models import InvestIndexador  # noqa: E402

PADRAO = [
    ("CDI", "CDI"),
    ("SELIC", "Selic"),
    ("IPCA", "IPCA"),
    ("PREFIXADO", "Prefixado"),
    ("IGPM", "IGP-M"),
]


def main():
    db = SessionLocal()
    try:
        for codigo, nome in PADRAO:
            if not db.query(InvestIndexador).filter(InvestIndexador.codigo == codigo).first():
                db.add(InvestIndexador(codigo=codigo, nome=nome))
        db.commit()
        print("Indexadores padrão verificados/inseridos.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
