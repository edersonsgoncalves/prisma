-- setup_production_schema.sql
-- Este script aplica as melhorias de integridade e padronização no novo banco Prisma.

USE prisma;

-- 1. Renomear tabelas inconsistentes para snake_case
RENAME TABLE faturasCartoes TO faturas_cartoes;

-- 2. Padronização de colunas em faturas_cartoes
ALTER TABLE faturas_cartoes 
    CHANGE COLUMN faturasCartoesId fatura_id INT NOT NULL AUTO_INCREMENT,
    CHANGE COLUMN faturasCartoesVinculado conta_id INT,
    CHANGE COLUMN faturasCartoesDtVencimento data_vencimento DATE,
    CHANGE COLUMN faturasCartoesFechamento data_fechamento DATE,
    CHANGE COLUMN faturasCartoesFechado fechado TINYINT(1) DEFAULT 0,
    CHANGE COLUMN faturasCartoesValor valor_total DECIMAL(15,2),
    CHANGE COLUMN faturasCartoesMesAno mes_referencia DATE;

-- 3. Padronização de colunas em contas_bancarias
ALTER TABLE contas_bancarias
    CHANGE COLUMN idcontas_bancarias conta_id INT NOT NULL AUTO_INCREMENT;

-- 4. Padronização de colunas em projetos
ALTER TABLE projetos
    CHANGE COLUMN projetos_id projeto_id INT NOT NULL AUTO_INCREMENT;

-- ─────────────────────────────────────────────────────────────────────────────
-- LIMPEZA DE DADOS ÓRFÃOS (Correção para erro 1452)
-- ─────────────────────────────────────────────────────────────────────────────

-- Limpar Categorias órfãs (3583 registros identificados)
UPDATE operacoes 
SET operacoes_categoria = NULL 
WHERE operacoes_categoria IS NOT NULL 
  AND operacoes_categoria NOT IN (SELECT categorias_id FROM categorias);

-- Limpar Contas órfãs (Prevenção)
UPDATE operacoes 
SET operacoes_conta = NULL 
WHERE operacoes_conta IS NOT NULL 
  AND operacoes_conta NOT IN (SELECT conta_id FROM contas_bancarias);

-- Limpar Faturas órfãs (Prevenção)
UPDATE operacoes 
SET operacoes_fatura = NULL 
WHERE operacoes_fatura IS NOT NULL 
  AND operacoes_fatura NOT IN (SELECT fatura_id FROM faturas_cartoes);

-- Limpar Projetos órfãos (Prevenção)
UPDATE operacoes 
SET operacoes_projeto = NULL 
WHERE operacoes_projeto IS NOT NULL 
  AND operacoes_projeto NOT IN (SELECT projeto_id FROM projetos);

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Adição de Foreign Keys (Integridade)
-- ─────────────────────────────────────────────────────────────────────────────

-- Operações -> Contas
ALTER TABLE operacoes 
    MODIFY operacoes_conta INT,
    ADD CONSTRAINT fk_operacoes_conta FOREIGN KEY (operacoes_conta) REFERENCES contas_bancarias(conta_id);

-- Operações -> Categorias
ALTER TABLE operacoes 
    MODIFY operacoes_categoria INT,
    ADD CONSTRAINT fk_operacoes_categoria FOREIGN KEY (operacoes_categoria) REFERENCES categorias(categorias_id);

-- Operações -> Faturas
ALTER TABLE operacoes 
    MODIFY operacoes_fatura INT,
    ADD CONSTRAINT fk_operacoes_fatura FOREIGN KEY (operacoes_fatura) REFERENCES faturas_cartoes(fatura_id);

-- Operações -> Projetos
ALTER TABLE operacoes 
    MODIFY operacoes_projeto INT,
    ADD CONSTRAINT fk_operacoes_projeto FOREIGN KEY (operacoes_projeto) REFERENCES projetos(projeto_id);

-- 6. Ajuste de Decimal em Operações
ALTER TABLE operacoes 
    MODIFY operacoes_valor DECIMAL(15,2);
