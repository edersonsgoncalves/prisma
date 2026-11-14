"""Provider interface for read-only bank data ingestion."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass
class ExternalAccount:
    provider: str
    external_id: str
    name: str
    type: str = ""
    currency: str = "BRL"
    balance: Optional[Decimal] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExternalTransaction:
    provider: str
    external_id: str
    account_external_id: str
    posted_at: date
    amount: Decimal
    description: str = ""
    currency: str = "BRL"
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class BankDataProvider(Protocol):
    """Read-only provider contract. Implementations must never initiate payments."""

    name: str

    def list_accounts(self, connection: dict[str, Any]) -> list[ExternalAccount]: ...

    def list_transactions(
        self,
        connection: dict[str, Any],
        account_external_id: str,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> list[ExternalTransaction]: ...
