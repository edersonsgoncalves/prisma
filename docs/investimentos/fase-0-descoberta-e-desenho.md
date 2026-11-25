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
- **Fundos:** FI e **FII** (com eventos específicos) desde o escopo inicial, com modelagem extensível a outras classes.
- **Moedas:** BRL, USD e EUR (prioritários), extensível a outras.

### 4.2 Casos de uso principais
- Cadastrar custodiantes/corretoras, carteiras e ativos/produtos (com moeda, indexador, vencimento/liquidez, regras de remuneração).
- Registrar operações com **controle detalhado**: corretora, quantidade de papéis/cotas, preço unitário, valor total, taxas/custos, preço médio e câmbio (quando aplicável).
- Manter posição por carteira/ativo com granularidade máxima (incluindo **lotes** e histórico), saldo/quantidade e custo.
- Atualizar/registrar preços, cotações e **taxas de câmbio** (histórico, preferencialmente automatizado com fallback).
- Registrar eventos: proventos/dividendos/JCP/rendimentos, amortizações, rendimentos de FII e eventos corporativos (bonificação, desdobramento, grupamento, subscrição/direitos, conforme aplicável).
- **Apoio fiscal:** registrar informações relevantes para declaração e apuração/pagamento de tributos (ex.: proventos/rendimentos, operações e resultados, retenções e dados necessários por classe de ativo), de forma rastreável.
- Apurar valorização e resultados (realizado/não realizado), consolidar em BRL e expor exposição por moeda (preço vs câmbio).
- Integrar aportes/resgates com o financeiro pessoal (contas e movimentações), com rastreabilidade e conciliação.

## 5. Modelo de domínio proposto (alto nível)

> Entidades lógicas do módulo (tabelas `invest_*`), sujeitas a refinamento na Fase 1. Campos monetários em `DECIMAL` com precisão definida; datas/instantes explícitos.

- **Instituição/Custodiante (`invest_instituicoes`)**: identificação da corretora/banco/custodiante.
- **Carteira/Conta de investimento (`invest_carteiras`)**: agrupador de posições; moeda base de exibição/consolidação (padrão BRL) e referência opcional à conta financeira vinculada (`conta_id` do domínio financeiro, sem FK direta).
- **Ativo/Produto (`invest_ativos`)**: tipo/classe (RF, caixinha, ação, ETF, FI…), emissor/identificadores (ticker/ISIN/código quando aplicável), **moeda do ativo**, indexador e parâmetros de remuneração/vencimento/liquidez.
- **Indexador (`invest_indexadores`)** e **séries/valores de índice** (ex.: CDI/Selic/IPCA) quando necessário para cálculo/registro de rentabilidade.
- **Moeda e câmbio (decidido: reuso de `contas_moedas` + `invest_taxas_cambio`)**: reutilizar o cadastro de moedas existente (`contas_moedas`) como referência de moedas, preservando o isolamento do domínio via uso por referência/mapeamento; manter no módulo o **histórico de taxas de câmbio** (`invest_taxas_cambio`) com pares (ex.: USD/BRL, EUR/BRL), data/hora de referência, tipo/fonte da taxa (negociação, fechamento/PTAX/referência, manual/automatizada) e valor para conversão e marcação.
- **Cotação/Preço do ativo (`invest_cotacoes`)**: preço/cota por data (e hora, se intraday), moeda da cotação e fonte.
- **Posição (`invest_posicoes`)** e **lotes (`invest_lotes`)**: controle detalhado por carteira/ativo/corretora, com quantidade, preço/custo, preço médio e saldo (na moeda original e referência em BRL), preservando histórico para apuração de resultados.
- **Movimentação/Ordem (`invest_movimentacoes`)**: tipo (aplicação, resgate, compra, venda, transferência interna, ajuste), corretora, data de negociação/liquidação, quantidade de papéis/cotas, preço unitário, valor total, taxas/custos, **taxa de câmbio aplicada**, valores equivalentes em BRL, carteira/ativo e referência de correlação com operação financeira (quando integrada).
- **Evento (`invest_eventos`)**: proventos/dividendos/JCP/rendimentos (incl. FII), amortizações, vencimentos e eventos corporativos (bonificação, desdobramento, grupamento, subscrição/direitos, conforme aplicável); valor/quantidade na moeda original, câmbio, impacto em posição/caixa e campos de apoio fiscal (ex.: retenções/bases) quando pertinentes.
- **Registro fiscal de apoio (`invest_fiscal` / campos associados, a detalhar):** informações rastreáveis para suporte à declaração e apuração/pagamento de tributos por operação/evento/competência.
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
- **Princípio (decidido):** aportes e resgates são tratados como **transferências de caixa** entre o financeiro e a carteira de investimento (valores efetivamente movimentados). O módulo de investimentos é a **fonte de verdade** de posições, valorização e resultados (realizados/não realizados); o financeiro reflete os fluxos de caixa.
- **Aportes:** transferência da conta de origem (financeiro) para a conta/carteira de investimento pelo valor aportado (convertido conforme moeda/câmbio quando aplicável), com referência cruzada (id de correlação) para rastreabilidade e idempotência. No módulo de investimentos, o aporte é alocado em caixa/aplicação.
- **Resgates:** transferência da carteira/conta de investimento para a conta de destino no financeiro pelo **valor efetivamente resgatado/liquidado**. 
  - **Lucro/prejuízo:** permanece registrado no módulo de investimentos. Ganhos/perdas **não realizados** apenas afetam a valorização da posição (sem transferência). Ganhos/perdas **realizados** materializam-se no valor líquido resgatado e, portanto, no montante transferido (ex.: aportou 1.000, resgatou 1.100 → transferência de 1.100; prejuízo realizado reduz o valor transferido). Não há lançamento automático duplicado de receita/despesa de P&L no financeiro, evitando inconsistência; o detalhamento e a base para apoio fiscal ficam no módulo de investimentos (Fase 5/escopo fiscal).
  - Resgates parciais seguem a mesma lógica (valor líquido do evento transferido).
- **Consistência e conciliação:** operações de integração idempotentes; reconciliação entre fluxo de transferências e caixa/movimentações do investimento. Eventos sem movimentação de caixa (proventos creditados em carteira mas não resgatados, marcação) não geram transferência automática — apenas quando houver liquidação para o financeiro.
- **Multi-moeda na integração:** registrar moeda do ativo/operação, moeda da conta financeira, taxa de câmbio de liquidação e equivalentes em BRL em ambas as pontas do fluxo, garantindo rastreabilidade da conversão.

## 8. Fontes de dados de mercado e câmbio

| Dado | Opções | Decisão/Proposta | Observação |
|---|---|---|---|
| Câmbio (USD/EUR↔BRL) | Manual, importação (CSV), API | **Automatizado via [BrasilAPI](https://brasilapi.com.br/docs)** (ex.: câmbio), com persistência de histórico em `invest_taxas_cambio` | Manter fallback para registro/ajuste manual e importação quando necessário |
| Preços/cotas de ativos | Manual, importação, API de mercado | **Automatizado quando houver cobertura viável** (avaliar endpoints/provedores adequados ao ativo), com fallback manual/importação | Cobertura varia por classe (ex.: RV/fundos/ETFs/FIIs); definição fina por tipo de ativo na Fase 1 |
| Índices (CDI/Selic/IPCA) | Manual, importação, API/fonte pública | **Automatizado quando disponível** (ex.: BrasilAPI/taxas) para apoio a cálculos de RF, com fallback manual/importação | Séries históricas conforme necessidade de cálculo |

> Implementação deve prever cache/rate-limit básico, registro da fonte/data da cotação e tolerância a indisponibilidade (uso de última cotação válida ou entrada manual).

## 9. Critérios de aceite da Fase 0
- Modelo de domínio e entidades principais definidos e revisados (incluindo multi-moeda, câmbio, lotes e apoio fiscal).
- Contrato de integração com finanças pessoais especificado (aportes/resgates como transferências de caixa, tratamento de lucros/perdas realizados vs não realizados, referências e consistência/idempotência).
- Regras de conversão, fonte de taxa (evento vs marcação), fallback e arredondamento documentadas.
- Estratégia de fontes de dados definida (automação via BrasilAPI com fallback manual/importação).
- Decisões iniciais de escopo e implementação registradas.

## 10. Decisões registradas
1. **Cotações/câmbio/índices:** **automatizado** via [BrasilAPI](https://brasilapi.com.br/docs) quando aplicável (câmbio prioritário; índices/cotações conforme cobertura), com fallback para registro/ajuste manual e importação.
2. **Integração financeira:** aportes e resgates como **transferências** entre financeiro e investimentos (fluxo de caixa). **Lucros/perdas não realizados** permanecem no módulo de investimentos (valorização); **lucros/perdas realizados** refletem-se no valor líquido transferido no resgate, sem duplicar P&L como lançamento automático no financeiro (fonte de verdade e apoio fiscal no módulo de investimentos).
3. **Granularidade:** **controle máximo** — corretora, quantidade de papéis/cotas, preço unitário, valor total, preço médio, lotes/histórico, taxas/custos e câmbio.
4. **Fundos:** incluir **FI e FII** desde o escopo inicial, com eventos específicos.
5. **Tributação:** **registrar tudo** necessário para apoio à declaração e apuração/pagamento de tributos (operações, eventos/proventos/rendimentos, retenções e resultados) de forma rastreável.
6. **Moedas:** **reutilizar `contas_moedas`** como cadastro/referência de moedas; histórico de câmbio e lógica de conversão permanecem no módulo (`invest_taxas_cambio`).

## 11. Próximos passos (entrada para Fase 1)
- Refinar o esquema físico das tabelas `invest_*` (incluindo lotes, eventos/FII e registros fiscais de apoio) e planejar migrações.
- Implementar cadastros base (instituições/corretoras, carteiras, ativos/produtos), reuso de moedas e **histórico/ingestão de câmbio** (BrasilAPI + fallback).
- Definir convenção operacional das transferências de integração (contas/tipos/referências e idempotência) e implementar o esqueleto do contrato de serviços internos com o financeiro.
- Mapear cobertura da BrasilAPI por dado necessário (câmbio/índices) e definir estratégia de fallback/importação para preços/cotas não cobertos.
