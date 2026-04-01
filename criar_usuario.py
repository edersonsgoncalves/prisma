"""
Script utilitário para criar o primeiro usuário no banco de dados do FinOrg.
Execute dentro do venv: python criar_usuario.py
"""
import sys
from app.database import SessionLocal
from app.models import Usuario
from app.auth import hash_senha

def criar_admin(login, senha):
    db = SessionLocal()
    try:
        # Verifica se já existe
        existe = db.query(Usuario).filter(Usuario.usuario_login == login).first()
        if existe:
            print(f"Erro: O usuário '{login}' já existe!")
            return

        novo_usuario = Usuario(
            usuario_login=login,
            usuario_senha=hash_senha(senha),
            usuario_nome="Administrador",
            usuario_ativo=1
        )
        
        db.add(novo_usuario)
        db.commit()
        print(f"Sucesso! Usuário '{login}' criado com a criptografia Bcrypt.")
    except Exception as e:
        print(f"Ocorreu um erro: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python criar_usuario.py <login> <senha>")
    else:
        criar_admin(sys.argv[1], sys.argv[2])
