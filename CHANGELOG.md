# Mudanças

## Duas IAs: rápida e especialista

- A escolha das IAs foi reorganizada em duas, com a função de cada uma explicada no guia e nos Ajustes:
  - **IA rápida** (a do dia a dia): conversa e pedidos simples. Recomendada por chave de API, por ser mais ágil.
  - **IA especialista** (mexe no PC, pelo Hermes): assinatura (mais barata se o uso for frequente) ou, agora,
    **chave de API** (Anthropic, OpenAI, OpenRouter, DeepSeek), com **limite de gasto por dia**.
- O gasto da especialista paga por uso aparece em *Uso e gastos* e na barra de status.
- Se a especialista estiver fora do ar ou no limite do dia, a rápida tenta e a Luna avisa.

## Memória que aprende (e se limpa)

- A **IA rápida** agora também lê o que a Luna sabe de você (nome, preferências, pessoas).
- **Revisão do dia**: com a Luna parada, ela relê as conversas e sugere o que lembrar. Você aprova ou descarta
  na aba **Aprendizado** (medido: sozinha, ela chegou a gravar um nome que era erro de transcrição).
- **Limpeza semanal**: sugere apagar lembranças repetidas, velhas ou inúteis. Também só com a sua aprovação.
- Na aba Aprendizado dá para ver tudo o que ela lembra e **esquecer** qualquer item.
- O roteador deixa na IA rápida conversa, recados e fatos que você conta de si; "lembra que…" continua indo à
  especialista, que grava na hora.

## Mac (beta)

- A Luna agora roda também no **Mac com Apple Silicon**: descobre o sistema sozinha e se ajusta.
  Adaptado a partir do fork de [felipyenzo7543-blip](https://github.com/felipyenzo7543-blip/luna-mac).
  - Abrir e instalar: `Luna.command` (o `instalar.py` serve aos dois sistemas).
  - Ouvido local na GPU da Apple (Whisper pelo MLX); o processador fica de reserva.
  - Volume, mídia, bloquear a tela, print, abrir apps de Aplicativos, atalho `Ctrl+Alt+L` e o modo Mini.
  - Textos da janela (microfone, requisitos) mudam para o Mac.
- `src/plataforma.py` junta num lugar só o que muda entre Windows e Mac.

## 0.1.0 — primeira versão pública

- Ativação só pelo nome "Luna" (detector no áudio + texto), com "cancela"/"para" para interromper.
- Comandos prontos sem IA (hora, volume, abrir programas e sites, timers…) e atalhos de voz.
- IA principal à escolha (DeepSeek, Gemini, OpenAI, Groq, OpenRouter, Ollama ou outra compatível); tarefas difíceis no Claude via Hermes Agent (modo Expert).
- Vozes Microsoft em pt-BR (Francisca, Thalita, Antônio) e o estilo JARVIS.
- Interface: conversas, aba Hoje (Meu Hoje), aba Aprendizado, paleta Ctrl+K, janela mini, Ctrl+Alt+L.
- Guia **Primeiros passos** com checagem do sistema e conexões (chaves no `.env`, nunca na tela).
- Trava de aprovação: mudanças da própria Luna só com testes + você.
- Instalação com um clique (`Luna.bat`) e atalho único "Luna".
- Botão **Atualização** na barra de cima: avisa quando sai versão nova e atualiza com um clique.
- Funciona **sem placa de vídeo**: a fala pode ser transcrita na nuvem (Groq, OpenAI ou Gemini).
- Ajustes reorganizados em abas: Voz, Escuta, Inteligência, Uso e gastos, Requisitos e dicas, Atalhos e Sistema.
- Tela inicial com exemplos para clicar; o guia de Primeiros passos termina ensinando como falar, como parar e os três modos.
- A voz não lê mais símbolos das respostas em markdown ("asterisco", "hífen", tabelas, links inteiros).
- **Treinar com a minha voz** (aba Aprendizado): o detector do nome aprende com as suas gravações, mostra se
  ficou melhor que o atual e só passa a valer com a sua aprovação.
- Instalação e abertura em Python puro (`instalar.py`, `src/abrir.py`): nenhum script PowerShell no projeto.
  Alguns antivírus acusavam os `.ps1` como ameaça (falso positivo).
- Modo Expert também com o **ChatGPT** (assinatura Plus/Pro pelo Hermes): GPT-6 Astra, GPT-5.6 Sol e GPT-5.6 Luna. A lista mostra só as assinaturas conectadas.
- "Luna, o que você sabe fazer?" responde com a lista real de comandos (conferida pelos testes) e os atalhos que ela aprendeu.
- Microfone bloqueado, ausente ou ocupado abre uma janela explicando onde clicar para liberar.
- README destaca o aprendizado com o Hermes (memória, habilidades e atalhos, com trava de aprovação).
- README com "Por que ela é diferente" e "Como usar" (tabela de frases de exemplo).
- Aba **Uso e gastos** (gasto de hoje, do mês, gráfico de 30 dias e quanto o seu uso custaria em cada IA) e
  **Requisitos e dicas** (o que a Luna usa no seu PC agora e como deixá-la mais leve ou mais barata).
