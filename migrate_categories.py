"""
Script de migração para unificar as tabelas 'categorias' e 'subcategorias'.
1. Adiciona a coluna 'categorias_pai_id' na tabela 'categorias'.
2. Migra os dados de 'subcategorias' para 'categorias'.
3. Remove a tabela 'subcategorias'.
"""
from app.database import engine, SessionLocal
from app.models import Categoria, Subcategoria
from sqlalchemy import text
import sys

def migrate():
    print("Iniciando migração de categorias...")
    
    # 1. Adicionar a coluna se ela não existir
    print("Passo 1: Verificando/Adicionando coluna 'categorias_pai_id'...")
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE categorias ADD COLUMN categorias_pai_id INT NULL"))
            conn.execute(text("ALTER TABLE categorias ADD CONSTRAINT fk_categorias_pai FOREIGN KEY (categorias_pai_id) REFERENCES categorias(categorias_id)"))
            conn.commit()
            print("Coluna e constraint adicionadas.")
        except Exception as e:
            if "Duplicate column name" in str(e) or "already exists" in str(e).lower():
                print("Coluna/Constraint já existe, ignorando.")
            else:
                print(f"Erro ao adicionar coluna: {e}")
    
    db = SessionLocal()
    try:
        # 2. Ler subcategorias
        print("Passo 2: Migrando dados da tabela 'subcategorias'...")
        # Usamos SQL puro para evitar problemas com mapeamento do SQLAlchemy se ele estiver instável
        with engine.connect() as conn:
            subs = conn.execute(text("SELECT subcategorias_id, subcategorias_nome, subcategorias_classe, categorias_pai FROM subcategorias")).fetchall()
        
        print(f"Encontradas {len(subs)} subcategorias para migrar.")
        
        # Carrega IDs de categorias existentes para validação de FK
        categorias_existentes = {c.categorias_id for c in db.query(Categoria.categorias_id).all()}
        
        migrados = 0
        for sub in subs:
            sid, nome, classe, pai = sub
            
            # Se o pai não existe em categorias, seta como NULL para evitar Inland IntegrityError
            if pai not in categorias_existentes:
                if pai is not None:
                    print(f"Aviso: Pai {pai} não existe para subcategoria '{nome}' ({sid}). Setando pai como NULL.")
                pai = None
            
            # Verifica se já existe uma categoria com este ID
            existe = db.query(Categoria).filter(Categoria.categorias_id == sid).first()
            if existe:
                if existe.categorias_pai_id is None and pai is not None:
                    existe.categorias_pai_id = pai
                    migrados += 1
                continue
                
            nova_cat = Categoria(
                categorias_id=sid,
                categorias_nome=nome,
                categorias_classe=classe,
                categorias_pai_id=pai
            )
            db.add(nova_cat)
            migrados += 1
        
        db.commit()
        print(f"Migração de dados concluída: {migrados} registros processados.")
        
        # 3. Renomear tabela antiga
        print("Passo 3: Backup da tabela 'subcategorias'...")
        with engine.connect() as conn:
            try:
                # Verifica se o backup já existe
                tables = conn.execute(text("SHOW TABLES")).fetchall()
                if ('_subcategorias_old',) not in tables:
                    conn.execute(text("RENAME TABLE subcategorias TO _subcategorias_old"))
                    conn.commit()
                    print("Tabela 'subcategorias' renomeada para '_subcategorias_old'.")
                else:
                    print("Backup '_subcategorias_old' já existe.")
            except Exception as e:
                print(f"Erro no backup: {e}")

    except Exception as e:
        db.rollback()
        print(f"ERRO CRÍTICO NA MIGRAÇÃO: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    migrate()
