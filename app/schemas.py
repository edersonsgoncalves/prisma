from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, datetime

class LancamentoBase(BaseModel):
    descricao: str
    valor: str  # String because it often comes with BRL formatting from masks
    data: date
    categoria: Optional[int] = None
    adicional_id: Optional[int] = None
    conta: int

class TransferenciaSchema(BaseModel):
    descricao: str
    conta: int
    conta_destino: int
    valor: str
    data: date
    fatura: Optional[int] = None
    modo_repeticao_modern_transf: Optional[str] = "unica"
    num_parcelas: Optional[int] = None
    parcela_inicial: Optional[int] = None
    frequencia: Optional[str] = "mensal"
    intervalo: Optional[int] = None
    valor_referencia: Optional[str] = None
    is_valor_parcela: Optional[str] = None
    adicional_id: Optional[int] = None
    efetivado: int = 0
    data_efetivado: Optional[datetime] = None
    next_url: Optional[str] = None

class LancamentoFaturaSchema(BaseModel):
    descricao: str
    conta: int
    valor: str
    tipo: int
    data: date
    fatura_id_origem: int
    categoria: Optional[int] = None
    adicional_id: Optional[int] = None
    modo_repeticao_modern: str
    num_parcelas: Optional[int] = None
    parcela_inicial: Optional[int] = 1
    intervalo: int = 1
