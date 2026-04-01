#!/bin/bash

# Define o caminho absoluto exato
PROJECT_DIR="/var/www/html/Prisma"

echo "📂 Acessando diretório do projeto: $PROJECT_DIR"

# Entra na pasta do projeto
if cd "$PROJECT_DIR"; then
    echo "🚀 Iniciando atualização do Prisma..."
else
    echo "❌ Erro: Não foi possível acessar $PROJECT_DIR"
    exit 1
fi

# 1. Build e Up
# O -d roda em background, o --build atualiza o código Python
docker compose up --build -d

# 2. Limpeza de imagens antigas
# Essencial para não encher o disco do servidor com versões obsoletas
echo "🧹 Removendo imagens não utilizadas..."
docker image prune -f

echo "✅ Prisma atualizado com sucesso!"
echo "📍 Acesse em: http://100.64.0.1:8000"

# 3. Verificação de logs
# Mostra se o FastAPI subiu e se conectou ao MySQL
echo "--- Verificando Logs do App ---"
sleep 2 # Pequena pausa para o container estabilizar
docker compose logs --tail=20 app