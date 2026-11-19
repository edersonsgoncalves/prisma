"""Cliente simples para integração com BrasilAPI (câmbio/índices) com fallback manual."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

import httpx

BASE_URL = "https://brasilapi.com.br/api"
TIMEOUT = 15.0


def _to_decimal(v: Any) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).date()
        except Exception:
            continue
    # tenta ISO parcial
    try:
        return date.fromisoformat(value[:10])
    except Exception:
        return None


def listar_moedas_cambio() -> list[dict]:
    """Lista moedas disponíveis para câmbio na BrasilAPI."""
    url = f"{BASE_URL}/cambio/v1/moedas"
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else []


def obter_cotacao_cambio(moeda: str, data_ref: date) -> dict:
    """Obtém cotação de câmbio para a moeda na data (usa endpoint /cambio/v1/cotacao/{moeda}/{data}).

    Retorna dict normalizado com chaves: moeda, data_referencia, cotacoes (lista bruta) e
    taxa_compra/taxa_venda/fechamento quando identificáveis.
    """
    # BrasilAPI aceita YYYY-MM-DD em versões recentes; tentamos esse formato
    data_str = data_ref.strftime("%Y-%m-%d")
    url = f"{BASE_URL}/cambio/v1/cotacao/{moeda.upper()}/{data_str}"
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()

    cotacoes = data.get("cotacoes") or []
    # heurística: usa última cotação do dia (geralmente fechamento/PTAX) se houver lista
    taxa_compra = taxa_venda = None
    if isinstance(cotacoes, list) and cotacoes:
        ultima = cotacoes[-1]
        taxa_compra = _to_decimal(ultima.get("cotacao_compra"))
        taxa_venda = _to_decimal(ultima.get("cotacao_venda"))
    else:
        # fallback para campos diretos se a API retornar formato diferente
        taxa_compra = _to_decimal(data.get("cotacao_compra") or data.get("compra"))
        taxa_venda = _to_decimal(data.get("cotacao_venda") or data.get("venda"))

    fechamento = taxa_venda or taxa_compra
    return {
        "moeda": data.get("moeda") or moeda.upper(),
        "data_referencia": _parse_date(data.get("data")) or data_ref,
        "taxa_compra": taxa_compra,
        "taxa_venda": taxa_venda,
        "fechamento": fechamento,
        "cotacoes": cotacoes,
        "bruto": data,
    }


def listar_taxas() -> list[dict]:
    """Lista taxas/índices disponíveis (ex.: CDI, Selic, IPCA) quando expostos pela BrasilAPI."""
    url = f"{BASE_URL}/taxas/v1"
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else []


def obter_taxa(sigla: str) -> dict:
    """Obtém valor atual de uma taxa/índice (ex.: CDI, Selic)."""
    url = f"{BASE_URL}/taxas/v1/{sigla}"
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, dict) else {"valor": data}
