# Como contribuir

Obrigado por querer melhorar a Luna!

## Rodando localmente

```powershell
git clone <seu fork> luna
cd luna
.\Luna.bat          # instala na primeira vez e abre
```

## Antes de abrir um PR

1. Rode os testes rápidos (não precisam de microfone):

   ```powershell
   $env:PYTHONIOENCODING='utf-8'
   .venv\Scripts\python.exe src\teste_comandos.py        # comandos prontos (sem rede)
   .venv\Scripts\python.exe src\teste_trava.py           # trava de aprovação
   .venv\Scripts\python.exe src\teste_roteador.py --rapido   # roteador (usa a IA configurada)
   ```

2. **Comando novo ou correção de comportamento?** Acrescente o exemplo no teste
   correspondente (`teste_comandos.py`, `teste_acoes.py` ou `teste_roteador.py`).
   Preferimos exemplos no roteador e atalhos em `atalhos.json` a regras de regex novas.
3. Nada de chaves, caminhos da sua máquina ou dados pessoais no código.
   Chaves vão no `.env` (fora do git).

## Estrutura

| Pasta | O quê |
|---|---|
| `src/servidor.py` | servidor (FastAPI + WebSocket): escuta, roteia, fala |
| `src/comandos.py` | comandos prontos (sem IA) |
| `src/roteador.py` | decide ação / conversa / Claude (IA escolhida) |
| `src/ativacao.py`, `src/detector_luna.py` | só atende quando ouve "Luna" |
| `src/fala.py`, `src/ouvido.py`, `src/escuta.py` | voz, transcrição (Whisper), detecção de fala (Silero) |
| `src/propostas.py` | trava de aprovação: mudanças da própria Luna passam por testes + você |
| `src/meuhoje.py` | integração opcional com o Meu Hoje |
| `web/index.html` | interface |
| `recursos/detector/` | detector genérico do nome |

## Estilo

- Código e comentários em português.
- Comentários explicam o **porquê** (o que foi medido, o que quebrava), não o quê.
