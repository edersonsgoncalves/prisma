"""
helpers.py — Funções utilitárias reutilizáveis em toda a aplicação.
Equivalente a dist/inc/funcoes.php
"""
from decimal import Decimal
from datetime import date
from typing import Optional


def formata_moeda_brl(valor: Optional[Decimal | float | str], simbolo: Optional[str] = "R$", abrev: Optional[str] = "BRL") -> str:
    """
    Formata número como moeda. 
    Se a abreviação for 'USD', usa formato americano (1,234.56).
    Caso contrário, usa formato brasileiro/europeu (1.234,56).
    """
    if simbolo is None or simbolo == "":
        simbolo = "R$"
    if abrev is None or abrev == "":
        abrev = "BRL"
        
    if valor is None or valor == "":
        return f"{simbolo} 0,00" if abrev != "USD" else f"{simbolo} 0.00"
        
    try:
        v = float(valor)
        sinal = "-" if v < 0 else ""
        
        # Define separadores baseado na abreviação (USD vs Resto)
        sep_milhar, sep_decimal = (",", ".") if abrev == "USD" else (".", ",")
        
        inteiro, decimal_part = f"{abs(v):.2f}".split(".")
        
        inteiro_fmt = ""
        for i, d in enumerate(reversed(inteiro)):
            if i and i % 3 == 0:
                inteiro_fmt = sep_milhar + inteiro_fmt
            inteiro_fmt = d + inteiro_fmt
            
        return f"{simbolo} {sinal}{inteiro_fmt}{sep_decimal}{decimal_part}"
    except (ValueError, TypeError):
        return f"{simbolo} 0,00" if abrev != "USD" else f"{simbolo} 0.00"


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


def formata_parcela(parcela: Optional[str | Decimal], por_extenso: Optional[bool] = False) -> str:
    """Transforma '002.012' em '2/12'"""
    if not parcela:
        return ""
    
    # Se for Decimal (ex: 2.012), converte para string primeiro
    p_str = str(parcela)
    partes = p_str.split(".")
    if len(partes) == 2:
        try:
            if por_extenso:
                return f"Parcela {int(partes[0])} de {int(partes[1])}"
            else:
                return f"{int(partes[0])}/{int(partes[1])}"
        except ValueError:
            return p_str
    return p_str

def date_today() -> str:
    return date.today().isoformat()


def bandeira_emoji(code: Optional[str]) -> str:
    """Converte código ISO (ex: 'br') em emoji de bandeira."""
    if not code:
        return ""
    code = str(code).strip().lower()
    if len(code) != 2:
        # Se não for um código de 2 letras, mas já for um emoji ou outra coisa, retorna como está
        return code if len(code) > 0 else ""
    try:
        # Regional Indicator Symbols: 'A' é 127462
        return "".join(chr(127462 + ord(c.upper()) - ord('A')) for c in code)
    except Exception:
        return ""
