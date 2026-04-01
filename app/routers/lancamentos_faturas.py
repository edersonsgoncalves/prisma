from datetime import date
from typing import Optional
from decimal import Decimal
import uuid

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from dateutil.relativedelta import relativedelta

from app.database import get_db
from app.auth import require_login
from app.models import Operacao, ContaBancaria, FaturaCartao, LogOperacao
from .lancamentos_utils import get_or_create_fatura, recalcular_total_fatura

router = APIRouter(prefix="/lancamentos", tags=["lancamentos"])

# Funções auxiliares movidas para lancamentos_utils.py

@router.post("/fatura/inserir/lancamento")
async def inserir_lancamento_fatura_especifico(
    request: Request,
    descricao: str = Form(...),
    conta: int = Form(...),
    valor: str = Form(...),
    tipo: int = Form(...),
    data_original: str = Form(alias="data"),
    fatura_id_origem: int = Form(...),
    categoria: Optional[str] = Form(default=None),
    adicional_id: Optional[int] = Form(default=None),
    modo_repeticao_modern: str = Form(...),
    num_parcelas: Optional[int] = Form(default=None),
    parcela_inicial: Optional[int] = Form(default=1),
    intervalo: int = Form(default=1),
    db: Session = Depends(get_db),
    sessao: dict = Depends(require_login),
):
    try:
        fatura_atual = db.query(FaturaCartao).filter(FaturaCartao.fatura_id == fatura_id_origem).first()
        if not fatura_atual:
            return RedirectResponse(url=request.headers.get("referer"), status_code=303)
        
        dt_ancora = fatura_atual.mes_referencia
        valor_limpo = valor.replace(".", "").replace(",", ".")
        valor_base = float(valor_limpo)
        valor_final = -abs(valor_base) if tipo == 3 else abs(valor_base)
        cat_id = int(categoria) if categoria and categoria != "-1" else None

        if modo_repeticao_modern == "parcelada" and num_parcelas:
            total_iterações = (num_parcelas - parcela_inicial) + 1
        elif modo_repeticao_modern == "fixa":
            total_iterações = 24
        else:
            total_iterações = 1

        grupo_id = f"FAT-{uuid.uuid4().hex[:8].upper()}" if total_iterações > 1 else None
        faturas_para_recalcular = set()
        novas_operacoes = []

        for i in range(total_iterações):
            idx_parcela_str = None
            salto_meses = i * (intervalo or 1)
            dt_destino = dt_ancora + relativedelta(months=salto_meses)

            if modo_repeticao_modern == "parcelada" and num_parcelas:
                num_atual = parcela_inicial + i
                idx_parcela_str = f"{num_atual:03d}.{num_parcelas:03d}"

            fat_id = get_or_create_fatura(db, conta, dt_destino)
            
            nova_op = Operacao(
                operacoes_data_lancamento=date.fromisoformat(data_original),
                operacoes_descricao=descricao,
                operacoes_conta=conta,
                operacoes_valor=valor_final,
                operacoes_tipo=tipo,
                operacoes_categoria=cat_id,
                operacoes_fatura=fat_id,
                operacoes_parcela=idx_parcela_str,
                operacoes_efetivado=0,
                operacoes_validacao=1,
                operacoes_grupo_id=grupo_id,
                operacoes_adicional_id=adicional_id
            )
            novas_operacoes.append(nova_op)
            if fat_id:
                faturas_para_recalcular.add(fat_id)

        db.add_all(novas_operacoes)
        db.commit()

        for f_id in faturas_para_recalcular:
            recalcular_total_fatura(db, f_id)

        return RedirectResponse(url=request.headers.get("referer"), status_code=303)

    except Exception as e:
        db.rollback()
        return RedirectResponse(url=request.headers.get("referer"), status_code=303)
