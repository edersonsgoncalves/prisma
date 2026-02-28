"""
helpers.py — Funções utilitárias reutilizáveis em toda a aplicação.
Equivalente a dist/inc/funcoes.php
"""
from pickletools import int4
from decimal import Decimal
from datetime import date
from typing import Optional


def formata_moeda_brl(valor: Optional[Decimal | float | str]) -> str:
    """Formata número como moeda BRL: R$ 1.234,56"""
    if valor is None or valor == "":
        return "R$ 0,00"
    try:
        v = float(valor)
        sinal = "-" if v < 0 else ""
        inteiro, decimal_part = f"{abs(v):.2f}".split(".")
        inteiro_fmt = ""
        for i, d in enumerate(reversed(inteiro)):
            if i and i % 3 == 0:
                inteiro_fmt = "." + inteiro_fmt
            inteiro_fmt = d + inteiro_fmt
        return f"R$ {sinal}{inteiro_fmt},{decimal_part}"
    except (ValueError, TypeError):
        return "R$ 0,00"


def mostra_data(data: Optional[date | str]) -> str:
    """Converte data para formato brasileiro: DD/MM/AAAA"""
    if not data:
        return ""
    if isinstance(data, str):
        if len(data) >= 10:
            return f"{data[8:10]}/{data[5:7]}/{data[0:4]}"
        return data
    return data.strftime("%d/%m/%Y")


def cor_valor(valor: Optional[Decimal | float]) -> str:
    """Retorna classe CSS conforme positivo/negativo/zero."""
    if valor is None:
        return "text-muted"
    v = float(valor)
    if v > 0:
        return "text-success"
    if v < 0:
        return "text-danger"
    return "text-muted"


NOMES_MESES = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
]


def mes_por_extenso(mes: int, ano: int) -> str:
    return f"{NOMES_MESES[mes]} / {ano}"


def formata_parcela(parcela: Optional[str | Decimal]) -> str:
    """Transforma '002.012' em '2/12'"""
    if not parcela:
        return ""
    
    # Se for Decimal (ex: 2.012), converte para string primeiro
    p_str = str(parcela)
    partes = p_str.split(".")
    if len(partes) == 2:
        try:
            return f"{int(partes[0])}/{int(partes[1])}"
        except ValueError:
            return p_str
    return p_str
