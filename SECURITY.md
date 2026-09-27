# Segurança

## Chaves e dados

- As chaves (da IA escolhida e do Hermes) ficam no arquivo `.env`, na pasta da Luna, **fora do git**.
  A tela nunca mostra a chave de volta: só os 4 últimos caracteres.
- O login do Meu Hoje fica em `dados/meuhoje/` (fora do git).
- Conversas e logs ficam em `dados/` e `logs/`, só no seu PC.
- O servidor da Luna escuta só em `127.0.0.1` (não aparece na rede).

**Nunca** cole o seu `.env`, os logs completos ou prints com chaves numa issue.

## A Luna mexendo no próprio código

Mudanças sugeridas pela própria Luna (comandos novos, ajustes) passam pela **trava de aprovação**
(`src/propostas.py`): ela roda os testes e só aplica depois que você aprova. Edição direta do código
sem commit é ignorada pelo servidor.

## Relatar uma falha

Achou uma falha de segurança? **Não abra issue pública.** Use
**Security → Report a vulnerability** (aviso privado do GitHub) neste repositório. Mantido por Lucas Tabosa (Krosten).
