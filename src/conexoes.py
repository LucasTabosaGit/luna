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
import subprocess
import urllib.request
from pathlib import Path

import config

ENV = config.RAIZ / ".env"

SERVICOS = {
    "hermes": {
        "nome": "Hermes Agent (as mãos da IA especialista)",
        "papel": "O programa que deixa a IA especialista mexer no PC: arquivos, apps, pesquisa, memória. Opcional.",
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
         | {"STT"} | {s["var"] for s in config.STTS.values() if s["var"]}
         | {"ESPECIALISTA_API", "ESPECIALISTA_LIMITE", "ESPECIALISTA_GRATIS"}
         | {e["var"] for e in config.ESPECIALISTAS_API.values()})
_ROTULO_HERMES = "luna"      # nome da chave que a Luna põe no cofre do Hermes


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
    return {"nome": "IA rápida (a do dia a dia)", "tipo": "cerebro", "obrigatorio": True,
            "papel": ("Conversa, perguntas, resumos e a decisão de chamar a especialista. "
                      "Responde quase tudo, então é a que mais gasta: prefira uma chave "
                      "de API barata (a assinatura passa pelo Hermes e fica mais lenta)."),
            "link": atual["link"], "escolhido": atual["id"],
            "modelo": config._env_valor("CEREBRO_MODELO"), "url": config._env_valor("CEREBRO_URL"),
            "opcoes": opcoes, "campos": [],
            "configurado": chave_ok and bool(atual["url"]) and bool(atual["modelo"])}


def _assinaturas() -> list[str]:
    """Assinaturas logadas no Hermes (só nomes; nenhum token é lido)."""
    logados = config._hermes_logados()
    saida = []
    if "anthropic" in logados and config.especialista_api() != "anthropic":
        saida.append("Claude")
    if "openai-codex" in logados:
        saida.append("ChatGPT")
    if "xai-oauth" in logados:
        saida.append("Grok")
    return saida


def _status_especialista() -> dict:
    api = config.especialista_api()
    assin = _assinaturas()
    hermes_ok = bool(config.chave_hermes())
    opcoes = []
    for ident, e in config.ESPECIALISTAS_API.items():
        k = _valor(e["var"])
        opcoes.append({"id": ident, "nome": e["nome"], "dica": e["dica"], "link": e["link"],
                       "var": e["var"], "definido": bool(k), "mostrar": _mascara(k)})
    return {"nome": "IA especialista (mexe no PC)", "tipo": "especialista", "obrigatorio": False,
            "papel": ("Entra só quando o pedido precisa agir no computador (arquivos, programas, "
                      "pesquisa longa) ou quando a IA rápida não dá conta. Roda pelo Hermes."),
            "hermes": hermes_ok, "assinaturas": assin, "modo": "api" if api else "assinatura",
            "escolhido": "openrouter-gratis" if config.especialista_gratis() else api,
            "gratis": config.especialista_gratis(),
            "opcoes": opcoes, "limite": config.especialista_limite_brl(),
            "configurado": hermes_ok and bool(assin or api)}


def _hermes_py() -> Path | None:
    """O Python do Hermes (ao lado do executável `hermes`)."""
    import plataforma
    nome = "python.exe" if os.name == "nt" else "python"
    exe = plataforma.hermes_exe()
    if exe:
        p = Path(exe).with_name(nome)
        if p.exists():
            return p
    # O `hermes` do PATH pode ser só um atalho (hermes.cmd em hermes\bin, posto
    # pelo app do Hermes): o Python de verdade fica no venv da instalação.
    pasta = "Scripts" if os.name == "nt" else "bin"
    p = plataforma.hermes_home() / "hermes-agent" / "venv" / pasta / nome
    return p if p.exists() else None


def _hermes_cli(args: list[str], entrada: str = "") -> tuple[bool, str]:
    """Roda `hermes -p <perfil> ...` sem console. A chave, quando há, vai
    pela entrada padrão (não aparece na lista de processos)."""
    import plataforma
    py = _hermes_py()
    if not py:
        return False, "Hermes não encontrado neste PC"
    cod = ("import sys\n"
           "a = sys.argv[1:]\n"
           "if '--api-key' in a:\n"
           "    a[a.index('--api-key') + 1] = sys.stdin.readline().strip()\n"
           "sys.argv = ['hermes'] + a\n"
           "from hermes_cli.main import main\n"
           "main()\n")
    env = dict(os.environ, HERMES_HOME=str(plataforma.hermes_home()), PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run([str(py), "-c", cod, "-p", config.HERMES_PERFIL, *args],
                           input=entrada, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, env=env, **plataforma.sem_janela())
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)[:100]
    return r.returncode == 0, (r.stdout + r.stderr).strip()[-200:]


def provedores_da_luna() -> list[str]:
    """Provedores em que a Luna deixou uma chave no cofre do Hermes (só os
    rótulos são lidos, nunca a chave)."""
    import plataforma
    arq = plataforma.hermes_home() / "profiles" / config.HERMES_PERFIL / "auth.json"
    try:
        pool = json.loads(arq.read_text(encoding="utf-8")).get("credential_pool") or {}
    except (OSError, ValueError):
        return []
    return [p for p, itens in pool.items()
            if any(isinstance(c, dict) and c.get("label") == _ROTULO_HERMES for c in itens)]


def registrar_especialista(prov: str) -> tuple[bool, str]:
    """Põe a chave (lida do .env da Luna) no cofre do Hermes, trocando a
    anterior da Luna. O Hermes lê o cofre a cada pedido: vale sem reiniciar."""
    e = config.ESPECIALISTAS_API[prov]
    k = _valor(e["var"])
    if not k:
        return False, "falta a chave"
    if prov in provedores_da_luna():
        _hermes_cli(["auth", "remove", prov, _ROTULO_HERMES])
    ok, saida = _hermes_cli(["auth", "add", prov, "--type", "api-key", "--label",
                             _ROTULO_HERMES, "--api-key", "-"], k + "\n")
    return ok, "" if ok else "o Hermes não aceitou a chave: " + saida.replace(k, "[chave]")[-120:]


def _testar_especialista() -> tuple[bool, str]:
    """Pedido de verdade pela IA especialista (confere Hermes + chave +
    modelo). Custa um pedido (~15 mil tokens), por isso só no "Salvar e
    testar", nunca ao abrir os Ajustes."""
    if not config.chave_hermes():
        return False, "conecte o Hermes primeiro"
    m = config.modelo_expert_efetivo("")
    corpo = {"model": config.HERMES_MODELO, "stream": False,
             "messages": [{"role": "user", "content": "Responda só: ok"}]}
    if m:
        corpo["model"], corpo["provider"] = m["id"], m["provedor"]
    import httpx
    try:
        r = httpx.post(config.HERMES_URL + "/chat/completions", json=corpo, timeout=120,
                       headers={"Authorization": "Bearer " + config.chave_hermes()})
    except httpx.HTTPError as ex:
        t = str(ex).lower()
        return False, ("o Hermes não respondeu (está aberto?)" if "connect" in t or "refused" in t
                       else "demorou demais para responder" if "timed out" in t else "sem conexão")
    try:
        j = r.json()
    except ValueError:
        return False, "resposta estranha do Hermes (HTTP %d)" % r.status_code
    if r.status_code != 200:
        return False, "erro do Hermes: %s" % str((j.get("error") or {}).get("message", ""))[:100]
    txt = str(((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    if "authentication failed" in txt.lower() or "no usable credentials" in txt.lower():
        return False, "chave recusada"
    if txt.startswith("⚠️"):
        return False, txt[:120]
    return True, "conectado (%s)" % (m["nome"] if m else "assinatura")


def _gpu() -> tuple[bool, float]:
    import plataforma
    g = plataforma.gpu()
    return bool(g["tipo"]), g["total_gb"]


def _placa_nao_nvidia() -> str:
    """Nome da placa AMD/Intel (Windows), para sugerir o ouvido Vulkan."""
    import plataforma
    if not plataforma.WINDOWS:
        return ""
    try:
        nomes = plataforma.placas_video()
    except Exception:  # noqa: BLE001
        return ""
    for n in nomes:
        b = n.lower()
        if "radeon" in b or "amd" in b or " arc" in b or b.startswith("arc"):
            return n
    return ""


def _status_stt() -> dict:
    atual = config.stt_id()
    tem_gpu, gb = _gpu()
    recomendado = "local" if tem_gpu and gb >= 4 else "groq"
    import plataforma
    outra = "" if tem_gpu else _placa_nao_nvidia()
    opcoes = []
    for ident, s in config.STTS.items():
        if ident == "vulkan" and not plataforma.WINDOWS:
            continue
        k = config.chave_stt(ident)
        opcoes.append({"id": ident, "nome": s["nome"], "dica": s["dica"], "link": s["link"],
                       "var": s["var"], "precisa_chave": bool(s["var"]), "definido": bool(k),
                       "mostrar": "" if not s["var"] else _mascara(k),
                       "recomendado": ident == recomendado})
    if atual == "local":
        placa = (("%s, %.0f GB" % ("placa de vídeo encontrada", gb)) if tem_gpu
                 else ("%s: sem CUDA, use Vulkan ou Groq" % outra) if outra else "sem placa de vídeo")
        configurado = True
    elif atual == "vulkan":
        placa = outra or "placa de vídeo pelo Vulkan"
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
    if motor == "vulkan":
        import ouvido_vulkan as ov
        if ov.ULTIMO_ERRO[0]:
            return False, "não funcionou (%s); usando o processador" % ov.ULTIMO_ERRO[0][:90]
        return True, "na placa pelo Vulkan (experimental)"
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
    elif nome == "especialista":
        ok, msg = _testar_especialista()
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
    saida = {"cerebro": _status_cerebro(), "stt": _status_stt(),
             "especialista": _status_especialista()}
    if testar_agora and saida["cerebro"]["configurado"]:
        saida["cerebro"]["teste"] = testar("cerebro")
    if testar_agora and saida["stt"]["configurado"]:
        saida["stt"]["teste"] = testar("stt")
    if testar_agora and saida["especialista"]["configurado"]:
        # Ao abrir os Ajustes: só "o Hermes está no ar?" (grátis). O pedido
        # de verdade (pago, na chave) fica para o "Salvar e testar".
        saida["especialista"]["teste"] = testar("hermes")
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
    saida["especialista"] = saida.pop("especialista")   # depois do Hermes: depende dele
    return saida


def salvar(valores: dict) -> dict:
    """{VAR: valor} → .env + ambiente do processo (vale sem reiniciar)."""
    for var, valor in valores.items():
        if var not in _VARS:
            raise ValueError("campo desconhecido: %s" % var)
        valor = (valor or "").strip().strip('"').strip("'")
        if var in ("CEREBRO_MODELO", "ESPECIALISTA_API", "ESPECIALISTA_GRATIS") and not valor:
            _gravar_env(var, "")          # vazio = padrão (modelo da IA / só assinatura)
            os.environ.pop(var, None)
            continue
        if var == "ESPECIALISTA_API" and valor not in config.ESPECIALISTAS_API:
            raise ValueError("opção desconhecida: %s" % valor)
        if var == "ESPECIALISTA_GRATIS":
            import gratis
            if not gratis.achar(valor):
                raise ValueError("esse modelo não está grátis agora: %s" % valor[:60])
        if var == "ESPECIALISTA_LIMITE":
            try:
                if not 0 <= float(valor.replace(",", ".")) <= 1000:
                    raise ValueError
            except ValueError:
                raise ValueError("limite por dia: use um número de 0 a 1000") from None
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
