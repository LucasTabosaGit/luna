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

## Por que ela é diferente

- **Fala português do Brasil de verdade.** Tudo foi feito e testado em PT-BR: a escuta, os comandos, as vozes e as respostas.
  Entende "abaixa o som", "pula essa", "me lembra do bolo em 40 minutos" e até nomes mal pronunciados ("abre o espotifai").
- **Aprende com você (com o Hermes).** Por trás dela roda o [Hermes Agent](https://hermes-agent.nousresearch.com/docs),
  e isso faz a Luna melhorar com o uso:
  - **lembra de você entre conversas**: preferências, nomes, o jeito que você gosta das coisas (memória do Hermes);
  - **cria habilidades**: quando o Claude resolve uma tarefa nova, ele guarda o passo a passo e da próxima vez já sabe fazer;
  - **transforma repetição em atalho**: o que você pede várias vezes passa a rodar em segundos, sem nem chamar o Claude.

  Tudo aparece na aba **Aprendizado**, e nada muda sozinho: mudanças nela mesma passam por uma **trava de aprovação**
  (ela propõe, testa e só vale depois que você aprova). Sem o Hermes, a Luna funciona normalmente, só não aprende.
- **Só atende quando ouve "Luna".** Um detector do nome roda no seu PC antes de qualquer coisa. TV, música e conversa
  da sala são descartadas antes de virar texto e não saem do computador.
- **É rápida onde dá e inteligente onde precisa.** Hora, volume, timer, contas, abrir apps e sites respondem na hora,
  sem IA. Perguntas vão para uma IA rápida e barata. Tarefas de verdade no PC (arquivos, pesquisa, textos) vão sozinhas
  para o **Claude**, via [Hermes Agent](https://hermes-agent.nousresearch.com/docs).
- **Você escolhe as peças.** A IA principal pode ser DeepSeek, Gemini, OpenAI, Groq, OpenRouter, Ollama (no seu PC)
  ou **a assinatura que você já paga** (ChatGPT Plus/Pro e outras, pelo Hermes). O ouvido pode rodar na placa de
  vídeo (grátis, sem internet) ou na nuvem (sem placa). Tudo se troca em Ajustes, sem reiniciar.
- **Mostra quanto custa.** Ajustes → *Uso e gastos* mostra o gasto de hoje e do mês e quanto o seu uso custaria em
  cada IA. Com o DeepSeek, o uso normal fica em centavos por mês.
- **É sua.** Código aberto (MIT), chaves só no seu PC, conversas salvas em arquivos seus.

## Como usar

1. Clique no microfone (ou aperte `Espaço`) e fale com o nome na frase: **"Luna, que horas são?"**
2. Fale do seu jeito, sem decorar comando. Alguns exemplos:

| Você diz | O que acontece |
|---|---|
| "Luna, abaixa o som" / "volume em 30" | muda o volume na hora |
| "Luna, pausa" / "pula essa" / "toca Legião Urbana no Spotify" | controla a música |
| "Luna, abre o Chrome" / "abre o YouTube" / "abre a pasta downloads" | abre programas, sites e pastas |
| "Luna, me lembra de tirar o bolo em 40 minutos" | timer com aviso falado |
| "Luna, quanto é 15% de 200?" / "vai chover hoje?" | contas e clima, sem IA |
| "Luna, me explica o que é inflação" | conversa com a IA escolhida |
| "Luna, organiza os PDFs da pasta Downloads" | o Claude faz a tarefa no PC (com o Hermes) |

3. Não sabe o que pedir? Diga **"Luna, o que você sabe fazer?"**: ela mostra a lista completa.
4. Para parar a qualquer momento: **"Luna, para"** ou **"cancela"**.
5. Também dá para digitar, colar um print (Ctrl+V) ou anexar um PDF e perguntar sobre ele.

**Os três modos** (botões ao lado do microfone):
- **Auto**: o normal. Escolhe sozinho entre comando, IA rápida ou Claude.
- **Expert**: tudo direto para o **Claude ou o ChatGPT** (com a assinatura que você já paga, pelo Hermes). Mais lento, mais capaz.
  Escolha qual em Ajustes → Inteligência → *Modelo do Expert*.
- **Live**: conversa falada de ida e volta com o Gemini.

**Atalhos de teclado**: `Ctrl+Alt+L` chama a Luna de qualquer programa · `Ctrl+K` busca qualquer ação ·
**Mini** deixa um globo flutuante com a legenda por cima das outras janelas.

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

1. Baixe o projeto: **Code → Download ZIP** e extraia, ou pelo Git (use o endereço **HTTPS**):

   ```powershell
   git clone https://github.com/LucasTabosaGit/luna.git
   ```
2. Clique duas vezes em **`Luna.bat`**.

Na primeira vez ele instala tudo (ambiente Python, dependências com PyTorch/CUDA, `.env`) e cria o atalho
**Luna** na área de trabalho e no menu Iniciar. Depois é só usar o atalho: ele liga o que faltar e abre a janela.
Os modelos (~3 GB) baixam sozinhos no primeiro uso.

Na janela abre o guia **Primeiros passos**: confere Python, ffmpeg, placa de vídeo e modelos, e leva você por
cada conexão — a IA principal, Claude via Hermes e Meu Hoje — com os links e o botão *Salvar e testar*.
Ele fica em **Ajustes → Primeiros passos** (ou Ctrl+K). Para desligar: **Ajustes → Desligar a Luna**.

Permita o microfone e diga "**Luna**, …".

### Ligar o Expert: Claude ou ChatGPT (opcional)

1. Instale o Hermes Agent e crie um perfil (ex.: `hermes profile create assistente`).
2. No `.env` do perfil: `API_SERVER_ENABLED=true` e anote a `API_SERVER_KEY`.
3. Rode `hermes gateway run`.
4. Na Luna: Ajustes → Inteligência → Expert: endereço `http://127.0.0.1:8642/p/assistente/v1` e a chave.
5. Entre com a sua assinatura: `hermes -p assistente model` e escolha **Anthropic** (Claude) ou
   **ChatGPT or Codex Subscription** (ChatGPT Plus/Pro). Pode conectar os dois; em *Modelo do Expert* aparecem
   só os que estão conectados.

### Meu Hoje (opcional)

A aba **Hoje** mostra tarefas, agenda e o seu dia do [Meu Hoje](https://meuhoje.com.br).
Crie a conta em https://meuhoje.com.br/cadastro e clique em **Conectar conta** na aba Hoje
(login oficial do Meu Hoje; a Luna guarda o acesso em `dados/meuhoje/`, fora do git).

### Sites e atalhos seus

- `pessoal/sites.json` — `{"meu site": "https://..."}` para "Luna, abre meu site".
- `atalhos.json` — atalhos de voz (começa vazio; a Luna propõe novos pela trava de aprovação).

### Problemas comuns

- **`git@github.com: Permission denied (publickey)`**: você usou o endereço **SSH**
  (`git@github.com:...`), que exige uma chave SSH cadastrada no seu GitHub. Não é vírus nem falta de acesso:
  use o endereço HTTPS acima ou baixe o ZIP.
- **Aviso azul do Windows ao abrir o `Luna.bat`** ("O Windows protegeu o computador"): aparece com todo arquivo
  baixado da internet que ainda não é conhecido. Clique em **Mais informações → Executar assim mesmo**.
  O projeto não tem nenhum programa `.exe` nem script PowerShell: o `Luna.bat` só procura o Python e roda o
  `instalar.py` (cria o ambiente e instala o `requirements.txt`) e depois o `src/abrir.py`. Tudo em texto aberto,
  para conferir. Na dúvida, envie o ZIP ao [VirusTotal](https://www.virustotal.com).

## Detector do nome

O detector que vem em `recursos/detector/` foi treinado só com vozes sintéticas: funciona para qualquer
pessoa. Com o uso, a Luna guarda falas curtas suas **só no seu PC** (`dados_luna/voz_real`, as 300 mais
novas com o nome e as 300 mais novas sem). Quando já tiver umas 40 de cada, vá em **Aprendizado → O nome
“Luna” com a sua voz → Treinar agora** (1 a 3 minutos, no processador):

- ela treina um detector novo com a sua voz e compara com o atual nas suas gravações mais novas, que o novo
  nunca ouviu ("reconheceu o nome em 41 de 42 vezes, acordou sem ser chamada 2 vezes");
- se o novo não for melhor, é descartado e nada muda;
- se for melhor, só passa a valer quando você clicar em **Usar o novo**, e dá para voltar ao anterior.

Isso melhora o reconhecimento do **nome**. A transcrição do resto da frase (Whisper ou nuvem) é a mesma para
todo mundo e não aprende com a sua pronúncia.

## Estrutura

```
Luna.bat              abrir (instala na primeira vez)
instalar.py           .venv + dependências + .env + atalho
src/abrir.py          liga Hermes (se houver) e o servidor; abre a janela (é o que o atalho chama)
src/criar_atalho.py   atalho "Luna" na área de trabalho e no menu Iniciar
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

As chaves ficam só no `.env` (fora do git). O detector do nome roda sempre no seu PC: o que não tem "Luna"
é descartado ali mesmo. Com o ouvido **na placa**, o áudio é transcrito na sua máquina; com o ouvido **na nuvem**,
só as frases com "Luna" são enviadas ao serviço escolhido (Groq, OpenAI ou Gemini). O texto dos pedidos vai para a
IA escolhida (e para o Claude, quando usado). Conversas ficam em `dados/`, também fora do git.

## Autor

Criado por **Lucas Tabosa** · Krosten.

## Licença

O código é [MIT](LICENSE) © 2026 Lucas Tabosa (Krosten). Modelos e serviços de terceiros têm licenças próprias e **não vêm no repositório**
(baixam na primeira vez) — veja [TERCEIROS.md](TERCEIROS.md). Resumo importante: os modelos de base do
detector (openWakeWord) são **CC BY-NC-SA 4.0, não comerciais**.

Segurança e chaves: [SECURITY.md](SECURITY.md) · Mudanças: [CHANGELOG.md](CHANGELOG.md) ·
Contribuir: [CONTRIBUTING.md](CONTRIBUTING.md)
