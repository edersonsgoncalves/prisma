"""
app/templates.py — Instância centralizada do Jinja2 para evitar duplicidade de configuração.
"""
from pathlib import Path
from fastapi.templating import Jinja2Templates
from app.helpers import (
    formata_moeda_brl, mostra_data, cor_valor, 
    mes_por_extenso, formata_parcela, date_today
)

BASE_DIR = Path(__file__).resolve().parent

# Única instância de templates para toda a aplicação
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Registra os helpers globais uma única vez
templates.env.globals.update(
    formata_moeda=formata_moeda_brl,
    mostra_data=mostra_data,
    cor_valor=cor_valor,
    mes_por_extenso=mes_por_extenso,
    formata_parcela=formata_parcela,
    date_today=date_today,
)
