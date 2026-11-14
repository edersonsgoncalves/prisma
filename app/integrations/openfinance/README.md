# Open Finance (Brasil) — integração (rascunho)

## Objetivo
Ler informações bancárias **somente leitura** (contas, saldos, transações) de forma automatizável, com consentimento explícito do titular.

## Realidade regulatória/técnica (resumo)
No Open Finance Brasil, o compartilhamento de dados cadastrais/transacionais envolve:
- papéis regulados (transmissora/receptora/iniciadora, conforme caso),
- API de **Consentimento** (`/consents`) e permissões/escopos,
- autenticação/autorização no padrão **FAPI/OAuth2** (e requisitos de segurança/mTLS/certificados conforme perfil),
- jornada de consentimento com o cliente e gestão de expiração/renovação/revogação.

Para um app pessoal/homelab, a integração *direta* como participante pode ser desproporcional. Caminhos práticos:
1. **Agregador/provedor autorizado** (ex.: serviço que abstrai conectores bancários/Open Finance) via API server-to-server + tokens armazenados com segurança.
2. **OFX/PDF** (já existentes no Prisma) com melhorias de automação operacional.
3. **Participação direta** no ecossistema (quando aplicável ao negócio/regulação).

## O que este pacote entrega
- `base.py`: contrato `BankDataProvider` (read-only) e DTOs normalizados.
- Próximos passos sugeridos: implementação de provider, persistência de conexões/consentimentos (tabela), rotas admin, agendamento (APScheduler) e reconciliação com `Operacao` usando estratégia similar ao importador OFX (`fitid`/chave externa + score).

## Segurança mínima
- Nunca logar tokens/segredos; usar `.env`/secret manager.
- Mapear conexão ↔ usuário/conta local; registrar consentimento e escopo.
- Somente leitura; sem iniciação de pagamento.
