# Fase 1 — Notas de implementação (fundação)

## Entregas iniciadas nesta etapa
- **Estrutura do módulo:** `app/investimentos/` (modelos, serviço, cliente BrasilAPI, router) e templates em `app/templates/investimentos/`.
- **Migração:** script `scripts/migrate_investimentos.py` para criação das tabelas `invest_*` (`python scripts/migrate_investimentos.py`).
- **Cadastros base (UI/rotas):** instituições/corretoras, carteiras (vínculo opcional à conta financeira e moeda base por referência), ativos/produtos (RF, caixinha, ação, ETF, FI, FII, indexador/moeda).
- **Moedas e câmbio:** reuso de `contas_moedas` e histórico em `invest_taxas_cambio`; atualização automatizada via BrasilAPI (USD/EUR→BRL por data) com fallback de registro manual.
- **Integração financeira (esqueleto):** `IntegracaoFinanceiraService` com contrato para aportes/resgates como transferências (validação de vínculo e intenção idempotente/rastreável) — geração efetiva do lançamento financeiro a implementar conforme convenção operacional.

## Cobertura BrasilAPI (mapeamento inicial)

| Necessidade | Endpoint BrasilAPI (referência) | Uso no módulo | Observações |
|---|---|---|---|
| Câmbio USD/EUR↔BRL por data | `GET /cambio/v1/cotacao/{moeda}/{data}` | Ingestão de taxa de fechamento/compra-venda para `invest_taxas_cambio` | Implementado com fallback manual; requer moeda cadastrada em `contas_moedas` |
| Moedas de câmbio disponíveis | `GET /cambio/v1/moedas` | Apoio/validação de moedas suportadas | Pode ser usado em evolução para UX/validação |
| Índices/taxas (CDI, Selic etc.) | `GET /taxas/v1`, `GET /taxas/v1/{sigla}` | Base para rentabilidade/indexadores (RF/caixinhas) | Cliente preparado; persistência/cálculo a integrar nos fluxos de RF |
| Cotações de ativos (ações/ETFs/FIIs) | (não padronizado na BrasilAPI para todos os ativos) | Marcação de preço de RV/fundos/FIIs | **Lacuna:** prever fallback principal via importação/registro manual (e avaliar provedor específico na evolução) |

## Convenção operacional de integração (rascunho para Fase 1/4)
- **Vínculo:** `invest_carteiras.conta_financeira_id` referencia `contas_bancarias.conta_id` (sem FK). Recomenda-se conta `tipo_conta=5` (Investimentos) como espelho de caixa.
- **Aporte:** transferência do financeiro → conta de investimento pelo valor aportado (evento de caixa). Movimento de investimento (`APORTE`) correlacionado por `id_correlacao`/`operacao_financeira_id`.
- **Resgate:** transferência da conta de investimento → financeiro pelo **valor líquido** resgatado; P&L realizado refletido no montante, P&L não realizado permanece na posição.
- **Idempotência/rastreabilidade:** toda integração deve carregar chave de correlação única e registrar vínculo entre movimento `invest_*` e operação financeira.
- **Multi-moeda:** registrar moeda de origem/destino, taxa de câmbio de liquidação e equivalentes em BRL em ambos os lados.

## Próximos incrementos (Fase 1)
- Refinar migrações/constraints conforme uso real e popular indexadores padrão (CDI, SELIC, IPCA, PREFIXADO).
- Implementar persistência/consulta de séries de índices e aplicação em RF/caixinhas.
- Implementar operações/movimentações, posições e lotes (compra/venda/aporte/resgate) com cálculo de preço médio e equivalentes em BRL.
- Concretizar criação idempotente de transferências no financeiro a partir do contrato de integração.
- Implementar importação/registro de preços e cotações para ativos não cobertos por API.
