-- check_orphans.sql
USE prisma;

-- Buscar operações com categorias inexistentes
SELECT 
    COUNT(*) as total_orfãos,
    GROUP_CONCAT(DISTINCT operacoes_categoria) as ids_inexistentes
FROM operacoes 
WHERE operacoes_categoria IS NOT NULL 
  AND operacoes_categoria NOT IN (SELECT categorias_id FROM categorias);

-- Detalhes dos órfãos para decisão
SELECT 
    operacoes_id, 
    operacoes_descricao, 
    operacoes_data_lancamento, 
    operacoes_categoria 
FROM operacoes 
WHERE operacoes_categoria IS NOT NULL 
  AND operacoes_categoria NOT IN (SELECT categorias_id FROM categorias)
LIMIT 20;
