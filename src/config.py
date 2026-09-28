"""Assistente de voz local em tempo real — configuração central.

Tudo mora na pasta do projeto (os modelos somam ~10 GB, melhor fora
do C: se ele estiver cheio). Os caches de HuggingFace/Torch são redirecionados
ANTES de qualquer import pesado: se um `import torch` acontecer antes,
ele já resolveu o caminho e passa a gravar em %USERPROFILE%\\.cache.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_MAC = sys.platform == "darwin"

RAIZ = Path(__file__).resolve().parent.parent
MODELOS = RAIZ / "modelos"
CACHE = RAIZ / "cache"
LOGS = RAIZ / "logs"

for _d in (MODELOS, CACHE, LOGS):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------
# Caches fora de C: — precisa vir antes de importar torch/transformers.
# ---------------------------------------------------------------------
os.environ.setdefault("HF_HOME", str(MODELOS / "hf"))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(MODELOS / "hf" / "hub"))
os.environ.setdefault("TORCH_HOME", str(MODELOS / "torch"))
os.environ.setdefault("XDG_CACHE_HOME", str(CACHE))
# Evita erro de autenticação quando se usa mirror/proxy do HF.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# ---------------------------------------------------------------------
# Áudio
# ---------------------------------------------------------------------
SAMPLE_RATE = 16000          # Whisper e Silero VAD trabalham em 16 kHz
BLOCO_MS = 32                # tamanho do bloco de captura
BLOCO = SAMPLE_RATE * BLOCO_MS // 1000

# Fim de fala. 3 s (padrão do Hermes) deixa a conversa arrastada; 0,6 s
# com VAD neural corta rápido sem engolir pausa entre palavras.
SILENCIO_FIM_S = 0.6
FALA_MIN_S = 0.25            # ignora estalo/tosse curta
# Interromper a resposta falando por cima: quanto de fala firme precisa.
# Menos que isso dispara com tosse/eco; mais, demora a calar.
BARGE_MIN_S = 0.4
ESPERA_MAX_S = 30

# ---------------------------------------------------------------------
# STT — Whisper via CTranslate2 (faster-whisper)
# ---------------------------------------------------------------------
# large-v3-turbo: mesmo encoder do large-v3 com decoder de 4 camadas.
# Em português rende bem melhor que o `small` e cabe folgado em 16 GB.
STT_MODELO = "deepdml/faster-whisper-large-v3-turbo-ct2"
STT_COMPUTE = "float16"      # int8_float16 se faltar VRAM
# Mac (Apple Silicon): o mesmo modelo, convertido para o MLX (GPU da Apple).
STT_MODELO_MLX = "mlx-community/whisper-large-v3-turbo"
STT_IDIOMA = "pt"

# ---------------------------------------------------------------------
# LLM — servido localmente pelo LM Studio (API OpenAI-compatible)
# ---------------------------------------------------------------------
# Dois cérebros possíveis, selecionáveis pela interface:
#
#   rápido   LM Studio local (qwen3.5-4b) — 0,39s até o 1º token,
#            mas só conversa: não vê arquivos nem executa nada.
#
#   hermes   API server do Hermes (porta 8642) — o MESMO agente do
#            terminal, com terminal, arquivos, web, memória e skills.
#            Custa 3-10s porque ele pensa e usa ferramentas.
#
# Para ligar o modo Hermes: API_SERVER_ENABLED=true no .env do Hermes
# e `hermes gateway run` (perfil default — só há um gateway por host).
# O cérebro rápido é o DeepSeek (API). O LM Studio saiu do projeto:
# a placa fica livre (só Whisper, vozes e RVC) e não há modelo para carregar.
# (O cérebro rápido é escolhido pela pessoa: ver CEREBROS mais abaixo.)

# Perfil próprio do Hermes para o assistente (memória, skills e sessões
# separadas de outros projetos). O gateway único do host atende cada
# perfil em /p/<perfil>/v1, com a chave do .env DESSE perfil.
HERMES_PERFIL = os.environ.get("HERMES_PERFIL", "assistente")
HERMES_URL = os.environ.get(
    "HERMES_URL", f"http://127.0.0.1:8642/p/{HERMES_PERFIL}/v1")
HERMES_MODELO = HERMES_PERFIL

# Modelos do Claude que a tela pode escolher para o cérebro Hermes.
#
# O API server aceita `model` + `provider` por requisição e executa o turno
# NAQUELE modelo (gateway/platforms/api_server.py, _request_agent_overrides:
# "An explicit ``provider`` is always honored"). Sem `provider` o Hermes
# ignoraria o `model` — clientes genéricos mandam "gpt-4o" — então aqui o
# provider vai sempre junto.
#
# id vazio = usa o modelo configurado no perfil do Hermes (o padrão dele).
HERMES_PROVEDOR = "anthropic"
# O Expert pode rodar no Claude OU no ChatGPT: é a assinatura que a pessoa
# conectou no Hermes (`hermes model` -> "ChatGPT or Codex Subscription").
# Cada modelo leva o seu provider; a tela só mostra os grupos que estão
# logados no Hermes (ver modelos_expert).
HERMES_MODELOS = [
    {"id": "", "nome": "Padrão do Hermes", "provedor": "", "grupo": ""},
    # Claude: conferidos respondendo pelo Hermes em 25/09 (o "model" da
    # resposta bate com o pedido). Ids com ponto: o Hermes normaliza.
    {"id": "claude-opus-5.5", "nome": "Opus 5.5 (mais forte)", "provedor": "anthropic", "grupo": "Claude"},
    {"id": "claude-opus-5", "nome": "Opus 5", "provedor": "anthropic", "grupo": "Claude"},
    {"id": "claude-fable-5.1", "nome": "Fable 5.1", "provedor": "anthropic", "grupo": "Claude"},
    {"id": "claude-sonnet-5", "nome": "Sonnet 5 (equilibrado)", "provedor": "anthropic", "grupo": "Claude"},
    {"id": "claude-haiku-4.5", "nome": "Haiku 4.5 (mais rápido e econômico)", "provedor": "anthropic", "grupo": "Claude"},
    # ChatGPT (assinatura Plus/Pro via Hermes): conferidos em 27/09.
    {"id": "gpt-6-astra", "nome": "GPT-6 Astra", "provedor": "openai-codex", "grupo": "ChatGPT"},
    {"id": "gpt-5.6-sol", "nome": "GPT-5.6 Sol", "provedor": "openai-codex", "grupo": "ChatGPT"},
    {"id": "gpt-5.6-luna", "nome": "GPT-5.6 Luna (mais rápido)", "provedor": "openai-codex", "grupo": "ChatGPT"},
    # Por CHAVE DE API (cobrado por uso). Só aparecem depois que a pessoa
    # conecta a chave em Ajustes (ver ESPECIALISTAS_API / modelos_expert).
    {"id": "gpt-5.6-luna", "nome": "GPT-5.6 Luna (mais barato)", "provedor": "openai-api", "grupo": "OpenAI (API)"},
    {"id": "gpt-5.6-terra", "nome": "GPT-5.6 Terra", "provedor": "openai-api", "grupo": "OpenAI (API)"},
    {"id": "gpt-5.6-sol", "nome": "GPT-5.6 Sol (mais forte)", "provedor": "openai-api", "grupo": "OpenAI (API)"},
    {"id": "anthropic/claude-sonnet-5", "nome": "Claude Sonnet 5", "provedor": "openrouter", "grupo": "OpenRouter (API)"},
    {"id": "openai/gpt-5.6-sol", "nome": "GPT-5.6 Sol", "provedor": "openrouter", "grupo": "OpenRouter (API)"},
    {"id": "deepseek-v4-pro", "nome": "DeepSeek V4 Pro (mais barato)", "provedor": "deepseek", "grupo": "DeepSeek (API)"},
]

# IA ESPECIALISTA por chave de API. A chave vai para o cofre do Hermes
# (`hermes -p assistente auth add <provedor> --type api-key`), que vale sem
# reiniciar; a Luna guarda uma cópia no .env dela só para mostrar "…abcd".
# Anthropic por chave usa os modelos do grupo "Claude" acima (mesmo
# provedor da assinatura). Preço: US$ por 1M tokens (entrada, saída), da
# tabela do próprio Hermes (agent/usage_pricing.py) em 28/09.
# CUIDADO: cada pedido leva ~15 mil tokens de instruções do Hermes, então
# até um "oi" custa. Por isso há um limite por dia (ESPECIALISTA_LIMITE).
ESPECIALISTAS_API = {
    "anthropic": {"nome": "Anthropic (Claude)", "var": "ANTHROPIC_API_KEY", "grupo": "Claude",
                  "modelo": "claude-sonnet-5", "link": "https://console.anthropic.com/settings/keys",
                  "dica": "O mais capaz para mexer no PC."},
    "openai-api": {"nome": "OpenAI (API)", "var": "OPENAI_API_KEY", "grupo": "OpenAI (API)",
                   "modelo": "gpt-5.6-luna", "link": "https://platform.openai.com/api-keys",
                   "dica": "A API é paga à parte da assinatura do ChatGPT."},
    "openrouter": {"nome": "OpenRouter", "var": "OPENROUTER_API_KEY", "grupo": "OpenRouter (API)",
                   "modelo": "anthropic/claude-sonnet-5", "link": "https://openrouter.ai/keys",
                   "dica": "Uma chave para Claude, GPT e outros."},
    "deepseek": {"nome": "DeepSeek", "var": "DEEPSEEK_API_KEY", "grupo": "DeepSeek (API)",
                 "modelo": "deepseek-v4-pro", "link": "https://platform.deepseek.com/api_keys",
                 "dica": "O mais barato, porém menos capaz em tarefas longas."},
}
PRECO_ESPECIALISTA = {       # US$ / 1M tokens (entrada, saída)
    "claude-opus-5.5": (5.0, 25.0), "claude-opus-5": (5.0, 25.0), "claude-fable-5.1": (3.0, 15.0),
    "claude-sonnet-5": (2.0, 10.0), "claude-haiku-4.5": (1.0, 5.0),
    "gpt-5.6-sol": (5.0, 30.0), "gpt-5.6-terra": (2.5, 15.0), "gpt-5.6-luna": (1.0, 6.0),
    "anthropic/claude-sonnet-5": (2.0, 10.0), "openai/gpt-5.6-sol": (2.0, 10.0),
    "deepseek-v4-pro": (0.66, 1.98),
}
ASSINATURAS = {"openai-codex", "xai-oauth", "nous", "qwen-oauth", "copilot"}


def especialista_api() -> str:
    """Provedor que a pessoa conectou por CHAVE na Luna ("" = só assinatura)."""
    v = _env_valor("ESPECIALISTA_API")
    return v if v in ESPECIALISTAS_API else ""


def especialista_limite_brl() -> float:
    """Teto de gasto por dia da IA especialista paga por uso (R$). 0 = sem teto."""
    try:
        return max(0.0, float(_env_valor("ESPECIALISTA_LIMITE").replace(",", ".") or 5))
    except ValueError:
        return 5.0


def modelo_por_uso(ident: str) -> bool:
    """O modelo do Expert é cobrado por uso (chave de API)? Assinatura = não.
    Claude conta como pago só quando a Luna conectou a chave da Anthropic."""
    m = modelo_da_chave(ident)          # aceita "provedor|id" ou só o id
    if not m or not m["provedor"]:
        return False
    if m["provedor"] == "anthropic":
        return especialista_api() == "anthropic"
    return m["provedor"] not in ASSINATURAS


def _hermes_logados() -> set[str]:
    """Providers com login no Hermes (só os NOMES, lidos dos auth.json;
    nenhum token é lido) + o provider padrão do perfil."""
    import plataforma
    base = plataforma.hermes_home()
    achados: set[str] = set()
    for arq in (base / "auth.json", base / "profiles" / HERMES_PERFIL / "auth.json"):
        try:
            d = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k in ("providers", "credential_pool"):
            if isinstance(d.get(k), dict):
                achados.update(d[k].keys())
    try:
        cfg = (base / "profiles" / HERMES_PERFIL / "config.yaml").read_text(encoding="utf-8")
        m = re.search(r"^model:\s*\n(?:\s+.*\n)*?\s+provider:\s*(\S+)", cfg, re.M)
        if m:
            achados.add(m.group(1).strip("'\""))
    except OSError:
        pass
    return achados


def modelos_expert() -> list[dict]:
    """Modelos do Expert que dá para usar AGORA: assinaturas logadas no
    Hermes + o provedor que a pessoa conectou por chave NA LUNA (um pool
    antigo no Hermes sem chave válida não conta)."""
    logados = _hermes_logados()
    api = especialista_api()
    saida = []
    for m in HERMES_MODELOS:
        p = m["provedor"]
        ok = (not p or (p in ASSINATURAS or p == "anthropic") and p in logados
              or p == api and p != "anthropic")
        if ok:
            saida.append({**m, "por_uso": modelo_por_uso(_chave_modelo(m)) if p else False,
                          "chave": _chave_modelo(m)})
    return saida


def _chave_modelo(m: dict) -> str:
    """Identidade única na tela: o mesmo id existe na assinatura e na API
    (gpt-5.6-sol), então a opção carrega provedor|id."""
    return (m["provedor"] + "|" + m["id"]) if m["provedor"] else ""


def modelo_da_chave(chave: str) -> dict | None:
    """'provedor|id' (ou '' = padrão do Hermes) -> entrada de HERMES_MODELOS."""
    if not chave:
        return HERMES_MODELOS[0]
    prov, _, ident = chave.partition("|")
    if not _:                      # formato antigo: só o id (assume o 1º provedor)
        ident, prov = chave, ""
    return next((m for m in HERMES_MODELOS if m["id"] == ident and (not prov or m["provedor"] == prov)), None)


def provedor_modelo(ident: str) -> str:
    m = modelo_da_chave(ident)
    return (m["provedor"] if m else "") or HERMES_PROVEDOR
HERMES_MODELO_PADRAO = os.environ.get("HERMES_MODELO_CLAUDE", "").strip()


def _tem_assinatura() -> bool:
    """Alguma assinatura logada no Hermes? (Claude conta só se não foi a
    Luna que pôs uma chave da Anthropic lá.)"""
    logados = _hermes_logados()
    if logados & ASSINATURAS:
        return True
    return "anthropic" in logados and especialista_api() != "anthropic"


def modelo_expert_efetivo(escolha: str) -> dict | None:
    """O modelo que a IA especialista vai usar de fato. None = padrão do
    perfil do Hermes (a assinatura). Sem assinatura e com chave conectada
    na Luna, o "padrão" vira o modelo recomendado dessa chave."""
    m = modelo_da_chave(escolha) if modelo_claude_valido(escolha) else None
    if m and m["provedor"]:
        return m
    api = especialista_api()
    if api and not _tem_assinatura():
        ident = ESPECIALISTAS_API[api]["modelo"]
        return next((x for x in HERMES_MODELOS if x["id"] == ident and x["provedor"] == api), None)
    return None


def modelo_claude_valido(escolha: str) -> bool:
    """A tela só pode pedir um modelo desta lista (nada de texto solto no corpo)."""
    return modelo_da_chave(escolha) is not None


def chave_hermes() -> str:
    """API_SERVER_KEY do perfil do Hermes, lida do .env dele."""
    k = (os.environ.get("API_SERVER_KEY") or "").strip()
    if k:
        return k
    import plataforma
    base = plataforma.hermes_home()
    for cand in (base / "profiles" / HERMES_PERFIL / ".env", base / ".env"):
        try:
            for linha in cand.read_text(encoding="utf-8",
                                        errors="replace").splitlines():
                if linha.strip().startswith("API_SERVER_KEY="):
                    return linha.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""

# Qwen3.5 é um modelo de RACIOCÍNIO: por padrão gasta o orçamento de
# tokens em `reasoning_content` antes de escrever a resposta. Medido
# aqui: com max_tokens=120 ele pensava os 120 tokens e devolvia conteúdo
# VAZIO — a conversa por voz ficava muda. Para voz, o raciocínio é puro
# custo de latência.
#
# Testei as quatro formas conhecidas de desligar, neste servidor:
#
#     padrão                    1º token NUNCA  (120 tokens de think)
#     chat_template_kwargs      1º token NUNCA  (120 tokens de think)
#     sufixo /no_think          1º token NUNCA  (120 tokens de think)
#     reasoning_effort="none"   1º token 0,59s  (0 tokens de think)  <-
#
# ---------------------------------------------------------------------
# Cérebro rápido: a IA que entende os pedidos, conversa e lê imagens.
# ---------------------------------------------------------------------
# A pessoa escolhe (Ajustes -> Conexões). Todas falam o formato da OpenAI
# (chat.completions), então o resto do código não muda de uma para outra.
# `extra` vai no corpo do pedido: desliga o "pensar" onde existe, porque
# em voz cada segundo conta. `preco` = US$ por 1M tokens (entrada, saída),
# só para a estimativa de gasto na barra de status.
#
# Medido aqui (pergunta curta, 1ª resposta): DeepSeek ~1,0 s;
# Gemini 3.1 Flash-Lite ~0,7 s.
CEREBROS = {
    "deepseek": {
        "nome": "DeepSeek", "url": "https://api.deepseek.com",
        "modelo": "deepseek-flash", "var": "DEEPSEEK_API_KEY",
        "extra": {"thinking": {"type": "disabled"}}, "preco": (0.15, 0.60),
        "link": "https://platform.deepseek.com/api_keys",
        "dica": "Recomendado: rápido, centavos por dia, lê imagens.",
    },
    "gemini": {
        "nome": "Google Gemini", "url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "modelo": "gemini-3.1-flash-lite", "var": "GEMINI_API_KEY",
        "extra": {"reasoning_effort": "none"}, "preco": (0.25, 1.50),
        "link": "https://aistudio.google.com/apikey",
        "dica": "Tem cota grátis para começar. Lê imagens.",
    },
    "openai": {
        "nome": "OpenAI (ChatGPT)", "url": "https://api.openai.com/v1",
        "modelo": "gpt-4.1-mini", "var": "OPENAI_API_KEY",
        "extra": {}, "preco": (0.40, 1.60),
        "link": "https://platform.openai.com/api-keys",
        "dica": "A API é paga à parte da assinatura do ChatGPT. Lê imagens.",
    },
    "groq": {
        "nome": "Groq", "url": "https://api.groq.com/openai/v1",
        "modelo": "llama-3.3-70b-versatile", "var": "GROQ_API_KEY",
        "extra": {}, "preco": (0.59, 0.79),
        "link": "https://console.groq.com/keys",
        "dica": "Muito rápido, com cota grátis. Não lê imagens.",
    },
    "openrouter": {
        "nome": "OpenRouter (vários modelos)", "url": "https://openrouter.ai/api/v1",
        "modelo": "deepseek/deepseek-v4.1-flash", "var": "OPENROUTER_API_KEY",
        "extra": {}, "preco": (0.035, 0.29),
        "link": "https://openrouter.ai/keys",
        "dica": "Uma chave para centenas de modelos (troque o nome do modelo).",
    },
    "assinatura": {
        "nome": "Minha assinatura (ChatGPT etc., via Hermes)", "url": "",
        "modelo": "", "var": "",
        "extra": {}, "preco": (0.0, 0.0),
        "link": "https://hermes-agent.nousresearch.com/docs/integrations/providers#subscription-plans-what-your-plan-pays-for",
        "dica": "Sem chave: usa a assinatura em que você entrou no Hermes (ChatGPT Plus/Pro, "
                "Claude Max, SuperGrok). Primeiro conecte o Hermes (próxima etapa) e rode "
                "hermes model. Mais lenta: 3 a 8 s por resposta.",
    },
    "ollama": {
        "nome": "Ollama (no seu PC, sem internet)", "url": "http://127.0.0.1:11434/v1",
        "modelo": "qwen3:4b", "var": "",
        "extra": {"reasoning_effort": "none"}, "preco": (0.0, 0.0),
        "link": "https://ollama.com/download",
        "dica": "Grátis e privado, mas usa a sua placa de vídeo e erra mais. "
                "Instale o Ollama e rode  ollama pull qwen3:4b",
    },
    "outro": {
        "nome": "Outro compatível com OpenAI", "url": "",
        "modelo": "", "var": "CEREBRO_API_KEY",
        "extra": {}, "preco": (0.0, 0.0),
        "link": "https://platform.openai.com/docs/api-reference/chat",
        "dica": "Qualquer serviço no formato da OpenAI: informe endereço, modelo e chave.",
    },
}


def _chave_env(*nomes: str) -> str:
    """Primeira chave válida do ambiente ou do .env do projeto."""
    for n in nomes:
        v = (os.environ.get(n) or "").strip().strip('"').strip("'")
        if len(v) >= 20 and not v.startswith("COLE_"):
            return v
    env = RAIZ / ".env"
    if env.exists():
        for linha in env.read_text(encoding="utf-8", errors="replace").splitlines():
            linha = linha.strip()
            for n in nomes:
                if linha.startswith(n + "="):
                    v = linha.split("=", 1)[1].strip().strip('"').strip("'")
                    if len(v) >= 20 and not v.startswith("COLE_"):
                        return v
    return ""


def chave_deepseek() -> str:
    return _chave_env("DEEPSEEK_API_KEY")


def _env_valor(nome: str) -> str:
    """Valor cru do ambiente ou do .env (sem a exigência de tamanho de chave)."""
    v = (os.environ.get(nome) or "").strip().strip('"').strip("'")
    if v:
        return v
    env = RAIZ / ".env"
    if env.exists():
        for linha in env.read_text(encoding="utf-8", errors="replace").splitlines():
            linha = linha.strip()
            if linha.startswith(nome + "="):
                return linha.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def cerebro_id() -> str:
    v = _env_valor("CEREBRO").lower()
    return v if v in CEREBROS else "deepseek"


def chave_cerebro(ident: str | None = None) -> str:
    """Chave da IA escolhida ("" se falta). O Ollama não usa chave."""
    ident = ident or cerebro_id()
    if ident == "assinatura":
        return chave_hermes()
    var = CEREBROS[ident]["var"]
    if not var:
        return "local"
    if ident == "outro":
        return _env_valor(var)
    return _chave_env(var, "GOOGLE_API_KEY") if ident == "gemini" else _chave_env(var)


def cerebro() -> dict:
    """A IA escolhida, com modelo e endereço já resolvidos."""
    ident = cerebro_id()
    c = dict(CEREBROS[ident], id=ident)
    c["modelo"] = _env_valor("CEREBRO_MODELO") or c["modelo"]
    if ident == "outro":
        c["url"] = _env_valor("CEREBRO_URL")
    elif ident == "assinatura":
        # O API server do Hermes: o turno roda no modelo do perfil, ou seja,
        # na assinatura em que a pessoa entrou com `hermes model`.
        c["url"] = _env_valor("HERMES_URL") or HERMES_URL
        c["modelo"] = HERMES_MODELO
    return c


# Onde a fala vira texto. "local" = Whisper na placa de vídeo (~2 GB de
# VRAM, grátis, sem internet). As da nuvem mandam só as frases que já
# passaram pelo detector do nome "Luna" (roda no processador): a conversa
# da sala não sai do PC. Sem internet, cai num Whisper pequeno no
# processador (STT_RESERVA), então os comandos prontos continuam.
# Preços (US$ por hora de fala, set/2026): Groq 0,04; OpenAI mini 0,18.
STTS = {
    "local": {
        "nome": "Na GPU do Mac (Whisper)" if _MAC else "Na placa de vídeo (Whisper)",
        "var": "", "url": "", "modelo": "", "link": "",
        "dica": ("Grátis e funciona sem internet. Usa a GPU do Mac (Apple Silicon) via MLX." if _MAC
                 else "Grátis e funciona sem internet. Usa cerca de 2 GB da placa de vídeo (NVIDIA)."),
    },
    # Placa AMD/Intel no Windows: whisper.cpp com Vulkan (ouvido_vulkan.py).
    # Experimental: feito sem placa AMD para testar.
    "vulkan": {
        "nome": "Na placa AMD/Intel (Vulkan, experimental)",
        "var": "", "url": "", "modelo": "", "link": "",
        "dica": "Grátis e sem internet, para placa AMD Radeon ou Intel Arc (driver atualizado). "
                "Na primeira vez baixa ~600 MB. Experimental: se não funcionar, a Luna usa o "
                "processador e você pode trocar para o Groq.",
    },
    "groq": {
        "nome": "Na nuvem: Groq", "var": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1", "modelo": "whisper-large-v3-turbo",
        "link": "https://console.groq.com/keys",
        "dica": "O mesmo Whisper, na nuvem: rápido (~0,4 s), tem cota grátis e depois custa "
                "centavos por hora de fala. Recomendado para quem não tem placa de vídeo.",
    },
    "openai": {
        "nome": "Na nuvem: OpenAI", "var": "OPENAI_API_KEY",
        "url": "https://api.openai.com/v1", "modelo": "gpt-4o-mini-transcribe",
        "link": "https://platform.openai.com/api-keys",
        "dica": "Muito preciso em português. Pago (cerca de US$ 0,18 por hora de fala).",
    },
    "gemini": {
        "nome": "Na nuvem: Google Gemini", "var": "GEMINI_API_KEY",
        "url": "https://generativelanguage.googleapis.com/v1beta", "modelo": "gemini-3.1-flash-lite",
        "link": "https://aistudio.google.com/apikey",
        "dica": "Usa a mesma chave grátis do Gemini. Um pouco mais lento (~1 s).",
    },
}
STT_RESERVA = "small"          # Whisper no processador quando a nuvem falha

# Ouvido "vulkan": binário compilado pelo workflow whisper-vulkan do repo
# público (a partir do whisper.cpp oficial) e modelo do Hugging Face.
VULKAN_BIN_URL = ("https://github.com/LucasTabosaGit/luna/releases/download/"
                  "whisper-vulkan-v1.9.4/whisper-vulkan-win-x64.zip")
VULKAN_BIN_SHA256 = "855dc710495b9281bced72d9999f0bc300da39803d0f18dee55ae33ff2a8ab2d"
VULKAN_MODELO_ARQ = "ggml-large-v3-turbo-q5_0.bin"
VULKAN_MODELO_URL = ("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
                     "ggml-large-v3-turbo-q5_0.bin")
VULKAN_MODELO_SHA256 = "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"


def stt_id() -> str:
    v = _env_valor("STT").lower()
    if v == "vulkan" and _MAC:          # só existe no Windows
        return "local"
    return v if v in STTS else "local"


def chave_stt(ident: str | None = None) -> str:
    ident = ident or stt_id()
    var = STTS[ident]["var"]
    if not var:
        return "local"
    return _chave_env(var, "GOOGLE_API_KEY") if ident == "gemini" else _chave_env(var)


def aplicar_cerebro() -> None:
    """Atualiza LLM_* (lidos pelo resto do código) com a escolha atual."""
    global LLM_URL, LLM_MODELO, LLM_EXTRA, LLM_API_KEY, LLM_NOME
    c = cerebro()
    LLM_URL, LLM_MODELO, LLM_EXTRA, LLM_NOME = c["url"], c["modelo"], c["extra"], c["nome"]
    LLM_API_KEY = chave_cerebro() or "sem-chave"

SISTEMA = (
    "Seu nome é Luna. "
    "Você é uma assistente de voz em português do Brasil. "
    "Responda em UMA ou DUAS frases curtas, em linguagem falada. "
    "Nunca use markdown, listas, emoji ou código: o texto vai direto "
    "para a síntese de voz e qualquer símbolo é lido em voz alta. "
    "Escreva números, valores e porcentagens POR EXTENSO — 'cento e "
    "vinte e nove reais', não 'R$ 129'; 'quarenta por cento', não "
    "'40%' — porque a voz atropela símbolos. "
    "Prefira frases terminadas em ponto final a frases longas com "
    "vírgulas: a pausa do ponto soa natural, a da vírgula não. "
    "Responda imediatamente, sem raciocinar antes."
)

# Pedido DIGITADO na tela: a resposta é lida, então pode ter formatação.
# Vai só na mensagem do turno (não no histórico). A voz fala só o começo.
NOTA_DIGITADO = (
    "\n\n[Digitado na tela, não falado: a resposta aparece escrita no chat. "
    "Pode usar markdown (listas, negrito, tabelas, blocos de código) quando "
    "ajudar, e números normais. Comece com UMA frase curta que resuma a "
    "resposta — só ela é lida em voz alta; os detalhes ficam na tela.]")
FALA_MAX_DIGITADO = 260   # caracteres falados, no máximo, de uma resposta digitada

# Instrução para o cérebro Hermes. O API server do Hermes SOMA esta
# mensagem ao prompt dele (não substitui): ele mantém ferramentas,
# memória e skills. Aqui só entra o que muda por ser voz.
SISTEMA_HERMES = (
    "Nesta conversa seu nome é Luna: é assim que o usuário chama a "
    "assistente de voz, e é como você se apresenta. "
    "Esta conversa é por VOZ: o usuário fala pelo microfone e ouve sua "
    "resposta sintetizada. Você está no computador dele (" + ("Mac" if _MAC else "Windows") + ") e pode "
    "usar suas ferramentas para agir. "
    "A resposta final deve ter no máximo duas ou três frases curtas, em "
    "português falado, sem markdown, listas, código, caminhos longos ou "
    "emoji — tudo é lido em voz alta. Escreva números por extenso. "
    "Depois de agir, diga em uma frase o que fez ou o que encontrou. "
    "Se o pedido for ambíguo, pergunte em uma frase em vez de adivinhar."
)

# ---------------------------------------------------------------------
# TTS — Kokoro-82M (Apache 2.0), voz pt-BR
# ---------------------------------------------------------------------
# Voz padrão da interface. A Leda (Gemini) é a mais natural em pt-BR
# entre as testadas; o custo é latência: ~3,6-4,5s por resposta contra
# ~0,8s do Kokoro. Medido em conversa real, não só em prévia.
#
# Para voltar ao mais rápido: TTS_VOZ = "pf_dora".
TTS_VOZ = "edge:pt-BR-FranciscaNeural"

# As vozes pt-BR disponíveis, com o motor de cada uma.
#
# Kokoro (local, GPU): 3 vozes, ~0,3s até o primeiro áudio. Sintéticas,
# mas imbatíveis em latência e funcionam offline.
#
# Edge TTS (Microsoft, online, grátis): vozes neurais de produção —
# são estas que aparecem nos vídeos do YouTube. Bem mais naturais, ao
# custo de ~1s a mais e de depender de internet. Medido nesta máquina:
#
#     pf_dora (kokoro)   0,29s     Antonio (edge)    1,11s
#     pm_alex (kokoro)   0,39s     Francisca (edge)  1,33s
#     pm_santa (kokoro)  0,38s     Thalita (edge)    1,33s
VOZES_PTBR = [
    # --- naturais (online) --------------------------------------------
    {"id": "edge:pt-BR-ThalitaMultilingualNeural", "nome": "Thalita",
     "tipo": "feminina jovem", "motor": "edge", "natural": True, "lat": 1.33},
    {"id": "edge:pt-BR-FranciscaNeural", "nome": "Francisca",
     "tipo": "feminina", "motor": "edge", "natural": True, "lat": 1.33},
    {"id": "edge:pt-BR-AntonioNeural", "nome": "Antônio",
     "tipo": "masculina", "motor": "edge", "natural": True, "lat": 1.11},
]
# Só vozes da Microsoft em pt-BR (e o estilo JARVIS em cima do Antônio).
# O Kokoro local continua SÓ como reserva muda de escolha: se a internet
# cair no meio da fala, ela não fica sem voz (TTS_VOZ_RESERVA).

# ---------------------------------------------------------------------
# Gemini TTS (Google) — opcional, exige chave
# ---------------------------------------------------------------------
# O Gemini 3.1 Flash TTS está em 2º lugar no ranking público de TTS
# (ELO 1206, atrás só do Inworld) e tem 30 vozes que falam português
# nativamente. É pago por token de áudio, então só entra no seletor
# quando há chave configurada.
#
# Para ativar: crie a chave em https://aistudio.google.com/apikey e
# ponha no .env do projeto (ou use Ajustes -> Conexões):
#
#     GEMINI_API_KEY=sua-chave-aqui
# Modelo TTS. Medido aqui, com billing ativo e streaming (4 amostras):
#
#     gemini-3.1-flash-tts-preview   média 2,79s  melhor 2,55s
#     gemini-3.8-flash-tts           média 3,77s  melhor 2,97s
#
# O 3.8 é o mais novo e pontua melhor em benchmarks de pronúncia, mas
# em conversa o que pesa é o tempo até a primeira palavra.
GEMINI_MODELO = os.environ.get("GEMINI_TTS_MODELO",
                               "gemini-3.1-flash-tts-preview")

# As 8 mais adequadas a diálogo, entre as 30 disponíveis. O nome da voz
# não é localizado: a mesma voz fala qualquer idioma, com o sotaque
# induzido pelo texto.
VOZES_GEMINI = [
    {"id": "gemini:Zephyr", "nome": "Zephyr", "tipo": "feminina clara"},
    {"id": "gemini:Kore", "nome": "Kore", "tipo": "feminina firme"},
    {"id": "gemini:Aoede", "nome": "Aoede", "tipo": "feminina leve"},
    {"id": "gemini:Leda", "nome": "Leda", "tipo": "feminina jovem"},
    {"id": "gemini:Puck", "nome": "Puck", "tipo": "masculina animada"},
    {"id": "gemini:Charon", "nome": "Charon", "tipo": "masculina grave"},
    {"id": "gemini:Fenrir", "nome": "Fenrir", "tipo": "masculina intensa"},
    {"id": "gemini:Orus", "nome": "Orus", "tipo": "masculina firme"},
]


def chave_gemini() -> str:
    """A chave do Gemini, do ambiente ou do .env do projeto.

    O `.env` é entregue com um placeholder. Sem esta checagem, o texto
    de exemplo seria tratado como chave e as vozes do Gemini apareceriam
    no seletor só para falhar com HTTP 400 na primeira frase.
    """
    def _valida(k: str) -> str:
        k = (k or "").strip().strip('"').strip("'")
        if not k or k.startswith("COLE_") or len(k) < 20:
            return ""
        return k

    k = _valida(os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY"))
    if k:
        return k
    env = RAIZ / ".env"
    if env.exists():
        for linha in env.read_text(encoding="utf-8", errors="replace").splitlines():
            linha = linha.strip()
            if linha.startswith("#") or "=" not in linha:
                continue
            if linha.startswith(("GEMINI_API_KEY", "GOOGLE_API_KEY")):
                k = _valida(linha.split("=", 1)[1])
                if k:
                    return k
    return ""


# ---------------------------------------------------------------------
# RVC — conversão de timbre (opcional)
# ---------------------------------------------------------------------
# O RVC pega o áudio do Kokoro e troca o TIMBRE, mantendo a entonação.
# Custa ~0,06s numa frase típica: a latência continua sendo a do Kokoro.
#
# Roda num SERVIÇO à parte porque `fairseq` (dependência do RVC) não
# funciona em Python 3.11+, e o servidor web roda em 3.11. O serviço
# usa .venv-rvc (Python 3.10) e conversa por HTTP local.
#
# Subir:  .venv-rvc\Scripts\python.exe src\rvc_servico.py
# (Mac: .venv-rvc/bin/python3; sem CUDA, o RVC roda no processador)
RVC_URL = os.environ.get("RVC_URL", "http://127.0.0.1:8778")


def vozes_rvc() -> list:
    """Modelos .pth presentes em modelos/rvc."""
    pasta = RAIZ / "modelos" / "rvc"
    if not pasta.exists():
        return []
    return sorted(p.stem for p in pasta.glob("*.pth"))


# Descrição de cada modelo RVC, para o seletor não mostrar só o nome do
# arquivo. Quem não estiver lá aparece como "voz convertida".
# Fica em modelos/rvc/descricoes.json (junto dos .pth, fora do git):
# {"NomeDoModelo": "firme", ...}. Os modelos RVC são de cada um.
def _descricoes_rvc() -> dict:
    import json as _json
    try:
        return _json.loads((RAIZ / "modelos" / "rvc" / "descricoes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


DESCRICAO_RVC = _descricoes_rvc()

# Bases do Edge usadas para a conversão RVC. São vozes neurais de
# produção: a prosódia delas é o que o RVC herda.
#
# O motivo de existir: com base no Kokoro, o RVC herdava uma cadência
# mecânica — "TTS prosody mistakes propagate; RVC adjusts timbre but
# cannot rewrite cadence" (arquitetura de referência). Com base no Edge,
# herda entonação de voz neural e aplica o timbre escolhido por cima.
#
# Só bases femininas, para converter em modelos femininos: cruzar
# gêneros força o RVC a deslocar muito o tom e degrada o resultado.
#
# Medido: Francisca 1,51s + RVC ~0,4s = ~1,9s
#         Thalita   2,69s + RVC ~1,1s = ~3,8s
BASES_EDGE = {
    "Francisca": {"voz": "pt-BR-FranciscaNeural", "lat": 1.9},
    "Thalita": {"voz": "pt-BR-ThalitaMultilingualNeural", "lat": 3.8},
}


# Estilo "JARVIS" (Homem de Ferro): voz masculina calma do Edge, um pouco
# mais grave e pausada, + filtro de "IA" (eco curto de sala metálica e um
# chorus leve). Não é a voz do filme (dublador/Paul Bettany): é o estilo.
# id: jarvis:<voz Edge>
JARVIS = {
    "pt-BR-AntonioNeural": "Antônio",
}
JARVIS_TOM = "-6Hz"
JARVIS_RITMO = -6          # % mais devagar: dicção de mordomo
JARVIS_FILTRO = (
    "highpass=f=110,lowpass=f=8500,"
    "aecho=0.85:0.55:14|27:0.22|0.12,"
    "chorus=0.8:0.85:22:0.22:0.25:1.2,"
    "acompressor=threshold=-20dB:ratio=3:attack=5:release=80,"
    "volume=1.5")


def rvc_instalado() -> bool:
    """RVC é opcional e baixado à parte: venv próprio (.venv-rvc, Python
    3.10) + modelos .pth em modelos/rvc. Sem os dois, as vozes nem aparecem."""
    import plataforma
    return plataforma.python_venv(".venv-rvc").exists() and bool(vozes_rvc())


def vozes_disponiveis() -> list:
    """Vozes do seletor: Microsoft (Edge) pt-BR + JARVIS pt-BR, que não usam
    a placa de vídeo. As convertidas (RVC) só aparecem se o RVC estiver
    instalado, e o serviço dele só liga enquanto uma delas estiver escolhida
    (~0,7 GB de VRAM medido e ~20 s para ligar)."""
    lista = list(VOZES_PTBR)
    for voz, nome in JARVIS.items():
        lista.insert(0, {"id": "jarvis:" + voz, "nome": "JARVIS · " + nome,
                         "tipo": "estilo Homem de Ferro", "motor": "jarvis",
                         "natural": True, "lat": 1.3})
    if rvc_instalado():
        modelos = [n for n in vozes_rvc() if n in DESCRICAO_RVC]
        for base, info in BASES_EDGE.items():
            for alvo in modelos:
                lista.append({"id": "mix:%s:%s" % (base, alvo), "nome": "%s · %s" % (alvo, base),
                              "tipo": DESCRICAO_RVC.get(alvo, "convertida"), "motor": "mix",
                              "natural": True, "pesada": True, "lat": info["lat"]})
    return lista
# Voz de emergência: para onde cair quando o motor online falha. Tem de
# ser SEMPRE local, senão o fallback aponta para o próprio motor que
# acabou de falhar — com TTS_VOZ="gemini:Leda", uma falha do Gemini
# cairia no Gemini de novo e o assistente ficaria mudo.
TTS_VOZ_RESERVA = "pf_dora"
TTS_SR = 24000
# 1.0 = ritmo natural do Kokoro. O RVC herda a prosódia da base ("TTS
# prosody mistakes propagate; RVC adjusts timbre but cannot rewrite
# cadence"), então acelerar ou desacelerar aqui é transferido para a
# voz convertida — melhor deixar a base no ritmo em que foi treinada.
TTS_VELOCIDADE = 1.0

# Tamanho mínimo de uma frase para ser sintetizada sozinha.
#
# A PRIMEIRA frase usa um mínimo menor (PRIMEIRA_MIN): quanto antes o
# áudio começar, menor a latência percebida. Medido: "Bom dia!" (8
# chars) esperava acumular 41 caracteres antes de ir para o TTS, o que
# somava o tempo de gerar o resto da frase à espera do usuário.
#
# Frases muito curtas ficam com prosódia picotada, por isso o limite
# não é zero — mas 8-10 caracteres já formam uma saudação completa.
FRASE_MIN_CHARS = 24
PRIMEIRA_MIN = 8


# Cérebro escolhido (Ajustes -> Conexões grava CEREBRO no .env e chama
# aplicar_cerebro() de novo: vale sem reiniciar).
LLM_URL = LLM_MODELO = LLM_NOME = LLM_API_KEY = ""
LLM_EXTRA: dict = {}
aplicar_cerebro()
