# Luna — assistente de voz em português

Assistente de voz para Windows que você chama pelo nome ("**Luna**, que horas são?").
Escuta na sua máquina (placa de vídeo) ou na nuvem, pensa com a IA que você escolher (**DeepSeek**, Gemini, OpenAI, Groq, OpenRouter ou Ollama local) e,
opcionalmente, passa o trabalho pesado para o **Claude** via [Hermes Agent](https://hermes-agent.nousresearch.com/docs):
mexer em arquivos, abrir programas, pesquisar, lembrar das coisas.

```
microfone → portão "Luna" (openWakeWord) → Whisper large-v3-turbo (placa de vídeo) ou transcrição na nuvem
          → comandos prontos / atalhos (instantâneo)
          → roteador (IA escolhida): ação | conversa | Claude
          → voz Microsoft pt-BR (Francisca, Thalita, Antônio, JARVIS) → alto-falante
```

## O que ela faz

- **Só atende quando ouve "Luna"** — TV, música e conversa na sala são descartadas antes de transcrever.
- **Comandos prontos**: abrir apps e sites, volume, mídia, pastas, hora, contas — sem IA, na hora.
- **Conversa** rápida pela IA escolhida, que também **lê imagens** (cole um print no chat; DeepSeek, Gemini e OpenAI).
- **Modo Expert**: tudo direto ao Claude (precisa do Hermes).
- **Aprende atalhos**: o que o Claude resolve várias vezes vira atalho — com **trava de aprovação**:
  ela propõe, testa, e só vale depois que você aprova.
- Interface com conversas salvas, markdown, anexos (imagem, PDF, texto), abas de Atividade e Aprendizado,
  barra com o gasto do dia, **Mini** (globo flutuante com legenda), **Ctrl+Alt+L** de qualquer lugar e **Ctrl+K**.

## Requisitos

Todos: **Windows 10/11**, **Python 3.11**, `ffmpeg` no PATH (`winget install ffmpeg`) e ~8 GB de disco.
Escolha o jeito que combina com o seu PC (dá para trocar depois em Ajustes):

| Jeito | Placa de vídeo | RAM | Internet | Custo |
|---|---|---|---|---|
| **Leve** (ouvido na nuvem) | não precisa | 4 GB | sim | a IA + o ouvido (Groq tem cota grátis) |
| **Completo** (ouvido na placa) | NVIDIA, 4 GB+ | 8 GB | sim | só a IA |
| **Sem internet** (Ollama + ouvido na placa) | NVIDIA, 8 GB+ | 16 GB | só para as vozes | grátis |

Medido numa RTX 5060 Ti: no modo Completo a Luna usa ~2,8 GB da placa (Whisper 2,2 + voz de reserva 0,6);
no modo Leve, praticamente nada. As vozes convertidas (RVC, opcionais) somam ~0,7 GB só enquanto escolhidas.

**IA principal** (obrigatória, escolha uma): **DeepSeek** (recomendado, centavos por mês), **Gemini** (cota grátis),
OpenAI, Groq, OpenRouter, **Ollama** no próprio PC (sem chave) ou **a sua assinatura** (ChatGPT Plus/Pro, Claude Max,
SuperGrok) pelo Hermes, sem chave nenhuma. O guia de Primeiros passos tem o passo a passo para quem nunca pegou uma chave.

**Mais leve ou mais barato?** Ajustes → *Requisitos e dicas* mostra o que a Luna está usando no seu PC e sugere
o que trocar; *Uso e gastos* mostra quanto o seu uso custaria em cada IA.

Opcional: [Hermes Agent](https://hermes-agent.nousresearch.com/docs) (modo Expert/Claude).

## Instalar e abrir

1. Baixe o projeto (**Code → Download ZIP** e extraia, ou `git clone`).
2. Clique duas vezes em **`Luna.bat`**.

Na primeira vez ele instala tudo (ambiente Python, dependências com PyTorch/CUDA, `.env`) e cria o atalho
**Luna** na área de trabalho e no menu Iniciar. Depois é só usar o atalho: ele liga o que faltar e abre a janela.
Os modelos (~3 GB) baixam sozinhos no primeiro uso.

Na janela abre o guia **Primeiros passos**: confere Python, ffmpeg, placa de vídeo e modelos, e leva você por
cada conexão — a IA principal, Claude via Hermes e Meu Hoje — com os links e o botão *Salvar e testar*.
Ele fica em **Ajustes → Primeiros passos** (ou Ctrl+K). Para desligar: **Ajustes → Desligar a Luna**.

Permita o microfone e diga "**Luna**, …".

### Ligar o Claude (opcional)

1. Instale o Hermes Agent e crie um perfil (ex.: `hermes profile create assistente`).
2. No `.env` do perfil: `API_SERVER_ENABLED=true` e anote a `API_SERVER_KEY`.
3. Rode `hermes gateway run`.
4. Na Luna: Ajustes → Conexões → Claude: endereço `http://127.0.0.1:8642/p/assistente/v1` e a chave.

### Meu Hoje (opcional)

A aba **Hoje** mostra tarefas, agenda e o seu dia do [Meu Hoje](https://meuhoje.com.br).
Crie a conta em https://meuhoje.com.br/cadastro e clique em **Conectar conta** na aba Hoje
(login oficial do Meu Hoje; a Luna guarda o acesso em `dados/meuhoje/`, fora do git).

### Sites e atalhos seus

- `pessoal/sites.json` — `{"meu site": "https://..."}` para "Luna, abre meu site".
- `atalhos.json` — atalhos de voz (começa vazio; a Luna propõe novos pela trava de aprovação).

## Detector do nome

O detector que vem em `recursos/detector/` foi treinado só com vozes sintéticas: funciona para qualquer
pessoa. Para ficar mais preciso com a **sua** voz, grave amostras (a Luna guarda as chamadas em
`dados_luna/voz_real`) e rode `src/treina_detector_luna.py` — o modelo novo vai para `modelos/oww/luna.pkl`
e passa a ter prioridade.

## Estrutura

```
Luna.bat              abrir (instala na primeira vez)
iniciar.ps1           liga Hermes (se houver), vozes e servidor; abre a janela
instalar.ps1          .venv + dependências + .env + atalho
src/                  servidor e módulos (ver CONTRIBUTING.md)
web/index.html        interface
recursos/detector/    detector genérico do nome "Luna"
atalhos.json          atalhos de voz (começa vazio)
.env.exemplo          modelo das chaves (a tela preenche o .env)
```

## Testes

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe src	este_comandos.py            # comandos prontos (sem rede)
.venv\Scripts\python.exe src	este_roteador.py --rapido   # roteador (IA escolhida)
.venv\Scripts\python.exe src	este_so_luna.py             # só atende com "Luna" (servidor no ar)
```

Contribuições: veja [CONTRIBUTING.md](CONTRIBUTING.md).

## Privacidade

As chaves ficam só no `.env` (fora do git). O áudio é transcrito na sua máquina; o texto dos pedidos vai
para a IA escolhida (e para o Claude, quando usado). Conversas ficam em `dados/`, também fora do git.

## Autor

Criado por **Lucas Tabosa** · Krosten.

## Licença

O código é [MIT](LICENSE) © 2026 Lucas Tabosa (Krosten). Modelos e serviços de terceiros têm licenças próprias e **não vêm no repositório**
(baixam na primeira vez) — veja [TERCEIROS.md](TERCEIROS.md). Resumo importante: os modelos de base do
detector (openWakeWord) são **CC BY-NC-SA 4.0, não comerciais**.

Segurança e chaves: [SECURITY.md](SECURITY.md) · Mudanças: [CHANGELOG.md](CHANGELOG.md) ·
Contribuir: [CONTRIBUTING.md](CONTRIBUTING.md)
