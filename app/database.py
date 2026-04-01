"""
database.py — Configuração da conexão SQLAlchemy com o banco MySQL existente.
Utiliza pool de conexões e lê credenciais do .env com escape de caracteres.
"""
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os
import urllib.parse  # Importante para tratar caracteres especiais na senha

# Carrega .env da pasta raiz do projeto
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

# Captura as variáveis
user = os.getenv('DB_USER')
password = os.getenv('DB_PASS')  # Note que usei DB_PASS conforme seu código
host = os.getenv('DB_HOST')
port = os.getenv('DB_PORT', '3306')
db_name = os.getenv('DB_NAME')

# ESCAPE DA SENHA: Transforma caracteres como '@' em '%40', '*' em '%2A', etc.
safe_password = urllib.parse.quote_plus(password) if password else ""

# Montagem da URL robusta
DB_URL = (
    f"mysql+pymysql://{user}:{safe_password}"
    f"@{host}:{port}"
    f"/{db_name}?charset=utf8mb4"
)

engine = create_engine(
    DB_URL,
    pool_pre_ping=True,       # Verifica conexão antes de usar (essencial para Docker)
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