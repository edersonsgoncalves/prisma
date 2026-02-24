"""
database.py — Configuração da conexão SQLAlchemy com o banco MySQL existente.
Utiliza pool de conexões e lê credenciais do .env.
"""
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os

# Carrega .env da pasta py/
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

DB_URL = (
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', '3306')}"
    f"/{os.getenv('DB_NAME')}?charset=utf8mb4"
)

engine = create_engine(
    DB_URL,
    pool_pre_ping=True,       # Verifica conexão antes de usar
    pool_recycle=1800,        # Recicla conexões a cada 30min
    pool_size=10,
    max_overflow=20,
    echo=os.getenv('DEBUG', 'false').lower() == 'true',
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """Dependência FastAPI para injetar sessão do banco em cada request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
