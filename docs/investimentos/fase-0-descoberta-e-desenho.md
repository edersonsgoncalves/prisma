# Fase 0 — Descoberta e desenho (Módulo de Investimentos)

## 1. Objetivo
Definir escopo, fronteiras, modelo de domínio, estratégia multi-moeda/câmbio e contrato de integração com as finanças pessoais, estabelecendo a base para implementação incremental do módulo de investimentos.

## 2. Contexto atual (Prisma/FinOrg)
- **Contas financeiras:** já existe `tipo_conta = 5` (Investimentos) e suporte a moeda da conta (`contas_moedas` / `conta_moeda`). Contas de investimento podem representar o “caixa/posição financeira” vinculada ao módulo.
- **Operações:** tipos incluem `2 = Investimento` (além de 1 Receita, 3 Despesa, 4 Transferência, e 0 Pagamento de fatura no domínio de cartões). A UI possui pontos de extensão/placeholders para investimentos.
- **Arquitetura:** aplicação monolítica (FastAPI + Jinja2 + MySQL/SQLAlchemy) em repositório único, deploy no homelab.

## 3. Decisão: isolamento de domínio e dados
**Decisão proposta:** adotar **modular monolith** — **mesmo banco de dados do Prisma, com tabelas próprias e namespace/prefixo dedicado do módulo** (ex.: `invest_*`), e fronteira de domínio isolada no código.

- **Não** criar banco de dados separado nesta fase (evita complexidade operacional e transacional distribuída desnecessária agora).
- **Isolamento prático:**
  - Modelos, regras e cálculos encapsulados em módulo próprio (serviços/casos de uso do domínio de investimentos).
  - Acesso entre domínios mediado por **contrato/serviços internos (ou API interna)**, não por acoplamento difuso.
  - **Evitar FKs diretas** das tabelas `invest_*` para tabelas do domínio financeiro (e vice-versa) sempre que possível; usar identificadores de referência e validação por serviço para preservar independência e viabilizar extração futura.
- **Evolução:** o desenho permite, se necessário, migrar as tabelas para schema/banco separado ou extrair para serviço próprio com menor impacto.

## 4. Escopo funcional (visão alvo) e casos de uso

### 4.1 Produtos/ativos suportados
- **Renda Fixa:** CDB, LCI, LCA, Debêntures (prefixado, pós-fixado/CDI, híbrido/IPCA+, conforme aplicável).
- **Caixinhas:** aplicações em CDB com liquidez diária (saldo/rentabilidade diária, resgates).
- **Renda Variável:** Ações, ETFs.
- **Fundos:** FI (e, conforme evolução, FII/FIA/outros) — modelagem genérica por classe/tipo.
- **Moedas:** BRL, USD e EUR (prioritários), extensível a outras.

### 4.2 Casos de uso principais
- Cadastrar custodiantes, carteiras e ativos/produtos (com moeda, indexador, vencimento/liquidez, regras de remuneração).
- Registrar operações: aplicação/aporte, resgate/vencimento, compra, venda (quantidade, preço, taxas/custos, moeda e câmbio).
- Manter posição por carteira/ativo (e lotes quando necessário), saldo/quantidade e custo.
- Atualizar/registrar preços, cotações e **taxas de câmbio** (histórico).
- Registrar eventos: proventos/dividendos/JCP/rendimentos, amortizações e eventos corporativos (bonificação/desdobramento/grupamento) conforme aplicável.
- Apurar valorização e resultados (realizado/não realizado), consolidar em BRL e expor exposição por moeda.
- Integrar aportes/resgates com o financeiro pessoal (contas e movimentações), com rastreabilidade e conciliação.

## 5. Modelo de domínio proposto (alto nível)

> Entidades lógicas do módulo (tabelas `invest_*`), sujeitas a refinamento na Fase 1. Campos monetários em `DECIMAL` com precisão definida; datas/instantes explícitos.

- **Instituição/Custodiante (`invest_instituicoes`)**: identificação da corretora/banco/custodiante.
- **Carteira/Conta de investimento (`invest_carteiras`)**: agrupador de posições; moeda base de exibição/consolidação (padrão BRL) e referência opcional à conta financeira vinculada (`conta_id` do domínio financeiro, sem FK direta).
- **Ativo/Produto (`invest_ativos`)**: tipo/classe (RF, caixinha, ação, ETF, FI…), emissor/identificadores (ticker/ISIN/código quando aplicável), **moeda do ativo**, indexador e parâmetros de remuneração/vencimento/liquidez.
- **Indexador (`invest_indexadores`)** e **séries/valores de índice** (ex.: CDI/Selic/IPCA) quando necessário para cálculo/registro de rentabilidade.
- **Moeda e câmbio (`invest_moedas`/reuso controlado + `invest_taxas_cambio`)**: pares (ex.: USD/BRL, EUR/BRL), data/hora de referência, tipo/fonte da taxa (negociação, PTAX/fechamento, manual) e valor — histórico para conversão e marcação.
- **Cotação/Preço do ativo (`invest_cotacoes`)**: preço/cota por data (e hora, se intraday), moeda da cotação e fonte.
- **Posição (`invest_posicoes`)** e, quando aplicável, **lotes (`invest_lotes`)**: quantidade/saldo, preço médio/custo na moeda original e referência de custo em BRL, por carteira/ativo.
- **Movimentação/Ordem (`invest_movimentacoes`)**: tipo (aplicação, resgate, compra, venda, transferência interna, ajuste), data de negociação/liquidação, quantidade, preço/valor unitário e total na **moeda original**, taxas/custos, **taxa de câmbio aplicada**, valores equivalentes em BRL, carteira/ativo/conta de liquidação e referência de correlação com operação financeira (quando integrada).
- **Evento (`invest_eventos`)**: proventos/rendimentos, amortizações, vencimentos, bonificações/desdobramentos; valor/quantidade na moeda original, câmbio e impacto em posição/caixa.
- **Resultado/Marcações (derivado ou materializado, a definir na Fase 1):** P&L realizado e não realizado, com decomposição **variação de preço (moeda original)** x **variação cambial (conversão para BRL)**.

## 6. Multi-moeda, câmbio e regras de cálculo

### 6.1 Princípios
- **Registrar sempre a moeda original** da operação/ativo e a **taxa de câmbio** utilizada, além do equivalente em BRL.
- **Consolidação padrão em BRL**, com visões também na moeda original por ativo/carteira.
- **Separar efeitos:** resultado por variação do preço do ativo (em moeda do ativo) e por variação cambial na conversão para a moeda base.

### 6.2 Taxas e conversão
- **Taxa de evento (negociação/liquidação):** usada para converter aportes, resgates, compras, vendas e eventos na data em que ocorrem/settlement definido.
- **Taxa de marcação (valorização):** taxa de referência (ex.: fechamento/PTAX do dia ou última disponível) para cálculo de posição/resultado não realizado em BRL.
- **Fallback:** quando não houver taxa na data exata, usar regra explícita (última taxa útil disponível anterior) com indicação da origem/data da taxa.

### 6.3 Precisão e arredondamento
- Valores monetários: `DECIMAL` (precisão adequada, ex.: 18,6 ou conforme necessidade por ativo/moeda); quantidades com precisão compatível (ações fracionárias/cotas).
- Cálculos internos com precisão total; **arredondamento definido e consistente** (proposta: `ROUND_HALF_UP`) apenas na persistência/apresentação conforme regra por campo.
- Manter rastreabilidade: armazenar valor original, taxa e valor convertido para evitar divergência por recálculo.

## 7. Integração com finanças pessoais (contrato proposto)
- **Vínculo:** carteira de investimento associada a uma conta financeira (preferencialmente `tipo_conta = 5`), por referência de identificador (sem FK direta).
- **Aportes:** saída de recurso da conta origem e entrada/registro no caixa da carteira de investimento, refletida como movimentação financeira integrada (ex.: transferência entre contas e/ou operação de tipo investimento, conforme convenção a fechar na Fase 1), com referência cruzada (id de correlação/movimento de investimento) para rastreabilidade e idempotência.
- **Resgates:** movimento inverso, com câmbio/valores consistentes quando envolver moeda estrangeira.
- **Consistência:** operações de integração idempotentes, com conciliação e reconciliação de saldo (financeiro vs caixa investido); o módulo de investimentos é a **fonte de verdade** das posições/ativos, e o financeiro reflete os fluxos de caixa vinculados.
- **Multi-moeda na integração:** se a conta financeira for em BRL e o ativo/operação em USD/EUR (ou vice-versa), registrar moeda e câmbio em ambas as pontas do fluxo e o equivalente em BRL.

## 8. Fontes de dados de mercado e câmbio

| Dado | Opções | Proposta inicial (Fase 0→1) | Evolução |
|---|---|---|---|
| Câmbio (USD/EUR↔BRL) | Manual, importação (CSV), API (ex.: BCB/PTAX) | **Manual + importação**, com histórico em `invest_taxas_cambio` | Integração automática configurável |
| Preços/cotas de ativos | Manual, importação, API de mercado | **Manual + importação** | Integração por provedor conforme ativo |
| Índices (CDI/Selic/IPCA) | Manual, importação, API/fonte pública | **Manual/importação** conforme necessidade de cálculo | Automação |

> **Decisão em aberto:** provedores/frequência de atualização automática e política de credenciais/configuração (a definir antes da automação).

## 9. Critérios de aceite da Fase 0
- Modelo de domínio e entidades principais definidos e revisados (incluindo multi-moeda e câmbio).
- Contrato de integração com finanças pessoais especificado (fluxos de aporte/resgate, referências e regras de consistência/idempotência).
- Regras de conversão, fonte de taxa (evento vs marcação), fallback e arredondamento documentadas.
- Estratégia de fontes de dados (manual/importação/automação) definida para o MVP.
- Lista de decisões em aberto registrada para fechamento no início da Fase 1.

## 10. Questões em aberto (para decisão)
1. **Fonte de cotações/câmbio no MVP:** iniciar apenas manual/importação ou já priorizar integração automática (ex.: câmbio via BCB/PTAX)?
2. **Integração financeira:** aportes/resgates devem gerar automaticamente lançamentos no financeiro (e com qual convenção de tipo/contas) ou serem conciliados manualmente no início?
3. **Granularidade de posição:** controle apenas por posição consolidada ou também por **lotes** (necessário para preço médio/realizado mais preciso em RV)?
4. **Fundos no escopo inicial:** incluir apenas FI genérico ou já contemplar FII (e eventos específicos) na Fase 3?
5. **Tributação/indicadores fiscais:** registrar eventos/informações para apoio a IR (ex.: proventos, rendimentos RF) no MVP ou postergar?
6. **Reuso de `contas_moedas`:** reutilizar tabela existente como cadastro de moedas com extensão no módulo ou manter cadastro de moedas do módulo independente (com mapeamento) para preservar isolamento?

## 11. Próximos passos (entrada para Fase 1)
- Fechar as questões em aberto acima.
- Refinar o esquema físico das tabelas `invest_*` e migrações.
- Implementar cadastros base (instituições, carteiras, ativos), cadastro/histórico de câmbio e registro manual/importação de cotações.
- Definir e implementar o esqueleto do contrato de integração (serviços internos) com o financeiro.
