"""Conexões com as IAs: status, teste e gravação das chaves no .env.

Tela de Ajustes → Conexões (e o primeiro uso, quando falta o DeepSeek):
cada serviço tem um campo de chave, "Salvar e testar" e o link de onde
pegar a chave. A chave vai para o `.env` do projeto (fora do git) e
NUNCA volta para a tela: o status mostra só os 4 últimos caracteres.

Serviços:
  cerebro   a IA principal (obrigatória), à escolha: DeepSeek, Gemini,
            OpenAI, Groq, OpenRouter, Ollama (local) ou outra compatível
  stt       onde a fala vira texto: placa de vídeo (Whisper) ou nuvem
            (Groq, OpenAI, Gemini), para quem não tem placa boa
  hermes    Expert/Claude via Hermes Agent (opcional): endereço + chave
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

import config

ENV = config.RAIZ / ".env"

SERVICOS = {
    "hermes": {
        "nome": "Claude (via Hermes Agent)",
        "papel": "Modo Expert e tarefas no PC (arquivos, apps, memória). Opcional.",
        "campos": [
            {"var": "HERMES_URL", "rotulo": "Endereço", "segredo": False,
             "exemplo": "http://127.0.0.1:8642/p/assistente/v1"},
            {"var": "API_SERVER_KEY", "rotulo": "Chave do API server", "segredo": True},
        ],
        "link": "https://hermes-agent.nousresearch.com/docs",
        "obrigatorio": False,
    },
}
_VARS_CEREBRO = {"CEREBRO", "CEREBRO_MODELO", "CEREBRO_URL"} | {
    c["var"] for c in config.CEREBROS.values() if c["var"]}
_VARS = ({c["var"] for s in SERVICOS.values() for c in s["campos"]} | _VARS_CEREBRO
         | {"STT"} | {s["var"] for s in config.STTS.values() if s["var"]})


# ------------------------------------------------------------------ .env
def _ler_env() -> dict:
    vals = {}
    if ENV.exists():
        for linha in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=(.*)$", linha)
            if m:
                vals[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return vals


def _gravar_env(var: str, valor: str) -> None:
    """Troca (ou acrescenta) só a linha VAR=..., preservando comentários."""
    linhas = ENV.read_text(encoding="utf-8", errors="replace").splitlines() if ENV.exists() else [
        "# Chaves da Luna. NAO versionar nem compartilhar (ver .gitignore).", ""]
    feito = False
    for i, linha in enumerate(linhas):
        if re.match(r"\s*%s\s*=" % re.escape(var), linha):
            linhas[i] = "%s=%s" % (var, valor)
            feito = True
    if not feito:
        linhas.append("%s=%s" % (var, valor))
    tmp = ENV.with_suffix(".tmp")
    tmp.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    os.replace(tmp, ENV)


def _valor(var: str) -> str:
    for ident, c in config.CEREBROS.items():
        if c["var"] == var and c["var"]:
            return config.chave_cerebro(ident)
    if var == "API_SERVER_KEY":
        return config.chave_hermes()
    if var == "HERMES_URL":
        return config.HERMES_URL
    return _ler_env().get(var, "")


def _mascara(v: str) -> str:
    return ("…" + v[-4:]) if len(v) >= 8 else ""


# ------------------------------------------------------------------ testes
def _get(url: str, cab: dict, tempo: float = 8) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers=cab)
        urllib.request.urlopen(req, timeout=tempo).read(200)
        return True, "conectado"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "chave recusada"
        return False, "o serviço respondeu erro %d" % e.code
    except Exception as e:  # noqa: BLE001
        t = str(e).lower()
        if "refused" in t or "10061" in t:
            return False, "nada respondendo nesse endereço (o Hermes está aberto?)"
        if "timed out" in t:
            return False, "demorou demais para responder"
        return False, "sem conexão"


def _testar_cerebro() -> tuple[bool, str]:
    """Uma pergunta de verdade (confere chave E nome do modelo)."""
    c = config.cerebro()
    k = config.chave_cerebro()
    if not k:
        return False, ("conecte o Hermes primeiro (próxima etapa)" if c["id"] == "assinatura"
                       else "falta a chave")
    if not c["url"]:
        return False, "falta o endereço"
    if not c["modelo"]:
        return False, "falta o nome do modelo"
    from openai import OpenAI
    try:
        cli = OpenAI(base_url=c["url"], api_key=k, max_retries=0,
                     timeout=60 if c["id"] == "assinatura" else 15)
        cli.chat.completions.create(model=c["modelo"], max_tokens=5, extra_body=c["extra"],
                                    messages=[{"role": "user", "content": "Responda só: ok"}])
        return True, "conectado"
    except Exception as e:  # noqa: BLE001
        cod = getattr(e, "status_code", None)
        t = str(e).lower()
        if cod in (401, 403):
            return False, "chave recusada"
        if cod == 402 or "insufficient" in t or "quota" in t:
            return False, "sem saldo ou cota nessa conta"
        if cod == 404 or "not found" in t or "does not exist" in t:
            return False, "modelo não encontrado: confira o nome"
        if cod == 429:
            return False, "limite de uso atingido; tente em instantes"
        if cod == 400 and ("extra" in t or "unrecognized" in t or "reasoning" in t):
            return False, "o serviço recusou um ajuste do pedido (%s)" % str(e)[:80]
        if "connect" in t or "refused" in t or "10061" in t:
            return False, {"ollama": "nada respondendo (o Ollama está aberto?)",
                           "assinatura": "o Hermes não respondeu (está aberto? rode  hermes gateway run)"
                           }.get(c["id"], "sem conexão com o serviço")
        if "timed out" in t or "timeout" in t:
            return False, "demorou demais para responder"
        return False, "erro: %s" % str(e)[:100]


def _status_cerebro() -> dict:
    atual = config.cerebro()
    opcoes = []
    for ident, c in config.CEREBROS.items():
        k = config.chave_cerebro(ident)
        opcoes.append({"id": ident, "nome": c["nome"], "dica": c["dica"], "link": c["link"],
                       "modelo_padrao": c["modelo"], "precisa_chave": bool(c["var"]),
                       "precisa_url": ident == "outro", "definido": bool(k),
                       "sem_modelo": ident == "assinatura",
                       "mostrar": "" if not c["var"] else _mascara(k)})
    chave_ok = bool(config.chave_cerebro())
    return {"nome": "Cérebro (IA principal)", "tipo": "cerebro", "obrigatorio": True,
            "papel": "A IA que entende os pedidos e conversa. Escolha a que preferir.",
            "link": atual["link"], "escolhido": atual["id"],
            "modelo": config._env_valor("CEREBRO_MODELO"), "url": config._env_valor("CEREBRO_URL"),
            "opcoes": opcoes, "campos": [],
            "configurado": chave_ok and bool(atual["url"]) and bool(atual["modelo"])}


def _gpu() -> tuple[bool, float]:
    try:
        import torch
        if torch.cuda.is_available():
            return True, torch.cuda.get_device_properties(0).total_memory / 2**30
    except Exception:  # noqa: BLE001
        pass
    return False, 0.0


def _status_stt() -> dict:
    atual = config.stt_id()
    tem_gpu, gb = _gpu()
    recomendado = "local" if tem_gpu and gb >= 4 else "groq"
    opcoes = []
    for ident, s in config.STTS.items():
        k = config.chave_stt(ident)
        opcoes.append({"id": ident, "nome": s["nome"], "dica": s["dica"], "link": s["link"],
                       "var": s["var"], "precisa_chave": bool(s["var"]), "definido": bool(k),
                       "mostrar": "" if not s["var"] else _mascara(k),
                       "recomendado": ident == recomendado})
    if atual == "local":
        placa = ("%s, %.0f GB" % ("placa de vídeo encontrada", gb)) if tem_gpu else "sem placa de vídeo"
        configurado = True
    else:
        placa = "não usa a placa de vídeo"
        configurado = bool(config.chave_stt(atual))
    return {"nome": "Ouvido (sua fala vira texto)", "tipo": "stt", "obrigatorio": True,
            "papel": "Onde a sua fala é transcrita. Só as frases com “Luna” são enviadas.",
            "escolhido": atual, "opcoes": opcoes, "campos": [], "placa": placa,
            "tem_gpu": tem_gpu, "configurado": configurado}


def _testar_stt() -> tuple[bool, str]:
    motor = config.stt_id()
    if motor == "local":
        tem_gpu, _gb = _gpu()
        return (True, "na placa de vídeo") if tem_gpu else (
            False, "sem placa de vídeo: vai ficar lento; prefira a nuvem")
    if not config.chave_stt(motor):
        return False, "falta a chave"
    from ouvido import TranscritorNuvem
    try:
        TranscritorNuvem(motor).testar()
        return True, "conectado"
    except Exception as e:  # noqa: BLE001
        t = str(e)
        if "401" in t or "403" in t or "invalid" in t.lower() and "key" in t.lower():
            return False, "chave recusada"
        if "429" in t:
            return False, "cota esgotada"
        return False, "erro: %s" % t[:100]


def testar(nome: str) -> dict:
    if nome == "stt":
        ok, msg = _testar_stt()
    elif nome == "cerebro":
        ok, msg = _testar_cerebro()
    elif nome == "hermes":
        k = config.chave_hermes()
        if not k:
            return {"ok": False, "msg": "não configurado"}
        ok, msg = _get(config.HERMES_URL.rstrip("/") + "/models",
                       {"Authorization": "Bearer " + k}, 4)
    else:
        return {"ok": False, "msg": "serviço desconhecido"}
    return {"ok": ok, "msg": msg}


def status(testar_agora: bool = False) -> dict:
    """Para a tela: sem valores secretos (só máscara)."""
    saida = {"cerebro": _status_cerebro(), "stt": _status_stt()}
    if testar_agora and saida["cerebro"]["configurado"]:
        saida["cerebro"]["teste"] = testar("cerebro")
    if testar_agora and saida["stt"]["configurado"]:
        saida["stt"]["teste"] = testar("stt")
    for nome, s in SERVICOS.items():
        campos = []
        for c in s["campos"]:
            v = _valor(c["var"])
            campos.append({**c, "definido": bool(v),
                           "mostrar": _mascara(v) if c["segredo"] else v})
        item = {k: s[k] for k in ("nome", "papel", "link", "obrigatorio")}
        item["campos"] = campos
        item["configurado"] = all(c["definido"] for c in campos)
        if testar_agora and item["configurado"]:
            item["teste"] = testar(nome)
        saida[nome] = item
    return saida


def salvar(valores: dict) -> dict:
    """{VAR: valor} → .env + ambiente do processo (vale sem reiniciar)."""
    for var, valor in valores.items():
        if var not in _VARS:
            raise ValueError("campo desconhecido: %s" % var)
        valor = (valor or "").strip().strip('"').strip("'")
        if var == "CEREBRO_MODELO" and not valor:
            _gravar_env(var, "")          # vazio = modelo padrão da IA escolhida
            os.environ.pop(var, None)
            continue
        if not valor or "\n" in valor or len(valor) > 400:
            raise ValueError("valor inválido para %s" % var)
        if var == "CEREBRO" and valor not in config.CEREBROS:
            raise ValueError("IA desconhecida: %s" % valor)
        if var == "STT" and valor not in config.STTS:
            raise ValueError("opção desconhecida: %s" % valor)
        if var in ("HERMES_URL", "CEREBRO_URL") and not re.match(r"https?://", valor):
            raise ValueError("o endereço precisa começar com http:// ou https://")
        _gravar_env(var, valor)
        os.environ[var] = valor
        if var == "HERMES_URL":
            config.HERMES_URL = valor.rstrip("/")
    config.aplicar_cerebro()
    return {"ok": True}


if __name__ == "__main__":
    print(json.dumps(status(True), ensure_ascii=False, indent=1))
