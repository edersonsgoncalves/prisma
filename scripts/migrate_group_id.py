import sys
import os
from pathlib import Path
import uuid
from datetime import timedelta

# Adiciona o diretório raiz ao sys.path para importar app
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.database import engine, SessionLocal
from app.models import Operacao
from sqlalchemy import text

def migrate():
    print("Iniciando migração...")
    
    # 1. Adiciona a coluna se ela não existir
    with engine.connect() as conn:
        print("Verificando/Adicionando coluna operacoes_grupo_id...")
        try:
            conn.execute(text("ALTER TABLE operacoes ADD COLUMN operacoes_grupo_id VARCHAR(50) NULL AFTER operacoes_projeto"))
            conn.commit()
            print("Coluna adicionada com sucesso.")
        except Exception as e:
            if "Duplicate column name" in str(e):
                print("Coluna já existe.")
            else:
                print(f"Erro ao adicionar coluna: {e}")
                return

    db = SessionLocal()
    try:
        # 2. Popula grupo_id para RECORRÊNCIAS
        print("Populando grupo_id para lançamentos recorrentes...")
        recorrencias = db.query(Operacao.operacoes_recorrencia).filter(Operacao.operacoes_recorrencia.isnot(None)).distinct().all()
        for (rec_id,) in recorrencias:
            grupo_id = f"REC-{uuid.uuid4().hex[:10]}"
            db.query(Operacao).filter(Operacao.operacoes_recorrencia == rec_id).update({"operacoes_grupo_id": grupo_id})
        
        # 3. Popula grupo_id para PARCELADOS
        # Estratégia: Agrupar por descrição, conta e total de parcelas, com datas próximas
        print("Populando grupo_id para lançamentos parcelados...")
        parcelados = db.query(Operacao).filter(Operacao.operacoes_parcela.isnot(None), Operacao.operacoes_grupo_id.is_(None)).order_by(Operacao.operacoes_data_lancamento).all()
        
        # Mapa para rastrear grupos abertos: (descricao, conta, total_parcelas) -> grupo_id
        grupos_ativos = {}
        
        for op in parcelados:
            try:
                p_sep = "/" if "/" in str(op.operacoes_parcela) else "."
                parts = str(op.operacoes_parcela).split(p_sep)
                if len(parts) < 2: continue
                p_atual, p_total = parts[0], parts[1]
                key = (op.operacoes_descricao, op.operacoes_conta, p_total)
                
                if key not in grupos_ativos:
                    grupos_ativos[key] = f"PAR-{uuid.uuid4().hex[:10]}"
                
                op.operacoes_grupo_id = grupos_ativos[key]
            except:
                continue
                
        db.commit()
        print("Migração concluída com sucesso!")
        
    except Exception as e:
        print(f"Erro durante a migração dos dados: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate()
