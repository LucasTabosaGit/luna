"""Meu Hoje (https://meuhoje.com.br) na Luna: aba Hoje e resumo do dia.

Uma conta só, para a aba Hoje E para a IA especialista:
  - se o Hermes tem o Meu Hoje configurado (`mcp_servers.meuhoje` no perfil),
    o login mora no cofre dele (mcp-tokens/meuhoje.json) e as duas usam o
    mesmo. "Conectar conta" grava ali; "Desconectar" apaga ali e desliga o
    servidor no Hermes (`enabled: false`), senão a especialista continuava
    mexendo nas tarefas com o login antigo;
  - sem Hermes: login próprio da Luna em dados/meuhoje/ (fora do git).
  O login é OAuth 2.1 com PKCE e cliente registrado sozinho (registro dinâmico).
  Depois de conectar/desconectar, o servidor reinicia o gateway do Hermes
  (ele guarda a conexão aberta em memória).

Renovação do token: o refresh token é de USO ÚNICO. Quando o token é o do
Hermes, a renovação pega a MESMA trava que ele (`meuhoje.json.refresh.lock`,
plataforma.travar: 1 byte), relê o arquivo depois da trava e grava do jeito dele
(atômico, `expires_at` absoluto), para Luna e gateway não queimarem o token
um do outro. Se a renovação falhar, NÃO apaga nada: só avisa para reconectar.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
import time
import urllib.parse
from pathlib import Path

import httpx

import config
import plataforma

NOME = "Meu Hoje"
SITE = "https://meuhoje.com.br"
CADASTRO = SITE + "/cadastro"
URL_MCP = SITE + "/api/mcp"
URL_TOKEN = SITE + "/api/oauth/token"
URL_AUTORIZAR = SITE + "/autorizar"
URL_REGISTRO = SITE + "/api/oauth/registrar"
ESCOPOS = ("tarefas:ler tarefas:escrever agenda:ler agenda:escrever "
           "diario:ler diario:escrever check:registrar")
FOLGA_S = 90          # renova se faltar menos que isso

# 1) login próprio da Luna
PROPRIA = config.RAIZ / "dados" / "meuhoje"
# 2) login do Hermes (perfil da Luna)
HERMES = plataforma.hermes_home() / "profiles" / config.HERMES_PERFIL / "mcp-tokens"


class Desconectado(Exception):
    """Sem token válido: conectar a conta na aba Hoje."""


def _hermes_cfg() -> dict | None:
    """O bloco `mcp_servers.meuhoje` do perfil do Hermes (None = não configurado)."""
    arq = HERMES.parent / "config.yaml"
    try:
        import yaml
        cfg = yaml.safe_load(arq.read_text(encoding="utf-8")) or {}
    except (OSError, ImportError, ValueError):
        return None
    srv = (cfg.get("mcp_servers") or {}).get("meuhoje")
    return srv if isinstance(srv, dict) else None


def _hermes_ligado() -> bool:
    srv = _hermes_cfg()
    return bool(srv) and srv.get("enabled", True) is not False


def _hermes_config(ligado: bool) -> None:
    """Liga/desliga o Meu Hoje na IA especialista (pelo próprio Hermes)."""
    exe = plataforma.hermes_exe()
    if not exe or _hermes_cfg() is None:
        return
    import subprocess
    subprocess.run([exe, "-p", config.HERMES_PERFIL, "config", "set",
                    "mcp_servers.meuhoje.enabled", "true" if ligado else "false"],
                   capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                   **plataforma.sem_janela())


def _fonte() -> dict | None:
    """Onde está o token: {"nome", "token", "cliente", "trava"}."""
    opcoes = [("luna", PROPRIA, "token.json", "cliente.json")]
    if _hermes_ligado():                 # desligado no Hermes = desconectado lá
        opcoes.insert(0, ("hermes", HERMES, "meuhoje.json", "meuhoje.client.json"))
    for nome, pasta, tok, cli in opcoes:
        if (pasta / tok).exists():
            return {"nome": nome, "pasta": pasta, "token": pasta / tok,
                    "cliente": pasta / cli, "trava": pasta / (tok + ".refresh.lock")}
    return None


def _ler(f: dict) -> dict:
    try:
        return json.loads(f["token"].read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Desconectado("sem token do Meu Hoje") from e


def _valido(t: dict) -> bool:
    return bool(t.get("access_token")) and float(t.get("expires_at") or 0) - time.time() > FOLGA_S


def _gravar(arq: Path, dados: dict) -> None:
    arq.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=arq.parent, prefix=".meuhoje.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, default=str)
    os.replace(tmp, arq)


def _token_novo(resp: dict, antigo: dict | None = None) -> dict:
    dados = {k: v for k, v in resp.items() if v is not None}
    if "refresh_token" not in dados and antigo and antigo.get("refresh_token"):
        dados["refresh_token"] = antigo["refresh_token"]
    if dados.get("expires_in") is not None:
        dados["expires_at"] = time.time() + int(dados["expires_in"])
    if antigo and antigo.get("hermes_issuer"):
        dados["hermes_issuer"] = antigo["hermes_issuer"]
    return dados


def _renovar(f: dict) -> dict:
    fd = os.open(f["trava"], os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fim = time.monotonic() + 15
        while True:
            try:
                plataforma.travar(fd)
                break
            except OSError:
                if time.monotonic() > fim:
                    raise Desconectado("trava do token ocupada")
                time.sleep(0.2)
        try:
            t = _ler(f)                       # o outro pode ter renovado
            if _valido(t):
                return t
            cliente = json.loads(f["cliente"].read_text(encoding="utf-8"))
            r = httpx.post(URL_TOKEN, timeout=20, data={
                "grant_type": "refresh_token",
                "refresh_token": t.get("refresh_token", ""),
                "client_id": cliente.get("client_id", ""),
            })
            if r.status_code != 200:
                raise Desconectado("renovação recusada (%d)" % r.status_code)
            dados = _token_novo(r.json(), t)
            _gravar(f["token"], dados)
            return dados
        finally:
            try:
                os.lseek(fd, 0, 0)
                plataforma.destravar(fd)
            except OSError:
                pass
    finally:
        os.close(fd)


def _token() -> str:
    f = _fonte()
    if f is None:
        raise Desconectado("conta do Meu Hoje não conectada")
    t = _ler(f)
    if not _valido(t):
        t = _renovar(f)
    return t["access_token"]


# ------------------------------------------------------------------ status
def status(verificar: bool = False) -> dict:
    """conectado = há login salvo; verificar=True também pergunta ao Meu Hoje
    se ele ainda vale (`valido`). especialista = a IA especialista usa a conta."""
    base = {"conectado": False, "fonte": None, "valido": None,
            "especialista": False, "hermes": _hermes_cfg() is not None}
    f = _fonte()
    if f is None:
        return base
    try:
        t = _ler(f)
    except Desconectado:
        return base
    base.update(conectado=bool(t.get("refresh_token") or _valido(t)), fonte=f["nome"],
                especialista=f["nome"] == "hermes")
    if verificar and base["conectado"]:
        try:
            chamar("ver_hoje")
            base["valido"] = True
        except Desconectado:
            base["valido"] = False
        except Exception:  # noqa: BLE001 - site fora do ar: não dá para saber
            base["valido"] = None
    return base


# ------------------------------------------------------------------ login próprio
_pendentes: dict = {}      # state -> {"verificador", "redirect", "t"}


def iniciar_login(redirect: str) -> str:
    """URL para abrir no navegador. Registra o cliente da Luna se preciso."""
    arq_cli = PROPRIA / "cliente.json"
    cli = {}
    if arq_cli.exists():
        cli = json.loads(arq_cli.read_text(encoding="utf-8"))
    if redirect not in cli.get("redirect_uris", []):
        r = httpx.post(URL_REGISTRO, timeout=20, json={
            "client_name": "Luna (assistente de voz)",
            "redirect_uris": [redirect],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": ESCOPOS,
        })
        if r.status_code not in (200, 201):
            raise RuntimeError("o Meu Hoje recusou o registro (%d)" % r.status_code)
        cli = r.json()
        _gravar(arq_cli, cli)

    verificador = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(
        hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    estado = secrets.token_urlsafe(24)
    agora = time.time()
    for k in [k for k, v in _pendentes.items() if agora - v["t"] > 900]:
        _pendentes.pop(k, None)
    _pendentes[estado] = {"verificador": verificador, "redirect": redirect, "t": agora}
    return URL_AUTORIZAR + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": cli["client_id"],
        "redirect_uri": redirect,
        "scope": ESCOPOS,
        "state": estado,
        "code_challenge": desafio,
        "code_challenge_method": "S256",
        "resource": URL_MCP,
    })


def concluir_login(codigo: str, estado: str) -> None:
    p = _pendentes.pop(estado, None)
    if not p:
        raise RuntimeError("pedido de login expirado; tente conectar de novo")
    cli = json.loads((PROPRIA / "cliente.json").read_text(encoding="utf-8"))
    r = httpx.post(URL_TOKEN, timeout=20, data={
        "grant_type": "authorization_code",
        "code": codigo,
        "redirect_uri": p["redirect"],
        "client_id": cli["client_id"],
        "code_verifier": p["verificador"],
    })
    if r.status_code != 200:
        raise RuntimeError("o Meu Hoje recusou o código (%d)" % r.status_code)
    tok = _token_novo(r.json())
    if _hermes_cfg() is None:
        _gravar(PROPRIA / "token.json", tok)
        return
    # Hermes configurado: grava no cofre dele, no formato dele, e religa lá.
    try:
        meta = json.loads((HERMES / "meuhoje.meta.json").read_text(encoding="utf-8"))
        if meta.get("issuer"):
            tok["hermes_issuer"] = str(meta["issuer"]).rstrip("/")
            cli = {**cli, "issuer": tok["hermes_issuer"]}
    except (OSError, ValueError):
        pass
    _gravar(HERMES / "meuhoje.client.json", cli)
    _gravar(HERMES / "meuhoje.json", tok)
    (PROPRIA / "token.json").unlink(missing_ok=True)
    _hermes_config(True)


def desconectar() -> None:
    """Desconecta a conta da Luna INTEIRA: aba Hoje e IA especialista.
    A trava `.refresh.lock` do Hermes fica (ele exige: é presa ao arquivo)."""
    (PROPRIA / "token.json").unlink(missing_ok=True)
    if _hermes_cfg() is not None:
        for n in ("meuhoje.json", "meuhoje.client.json"):
            (HERMES / n).unlink(missing_ok=True)
        _hermes_config(False)


# ------------------------------------------------------------------ MCP
def _dados(r: httpx.Response) -> dict:
    txt = r.text
    if "text/event-stream" in r.headers.get("content-type", ""):
        linhas = [ln[5:].strip() for ln in txt.splitlines() if ln.startswith("data:")]
        txt = linhas[-1] if linhas else "{}"
    return json.loads(txt or "{}")


def chamar(ferramenta: str, argumentos: dict | None = None) -> str:
    """Chama uma ferramenta do MCP do Meu Hoje e devolve o texto da resposta."""
    tok = _token()
    cab = {"Authorization": "Bearer " + tok, "Content-Type": "application/json",
           "Accept": "application/json, text/event-stream"}
    with httpx.Client(timeout=25) as c:
        r = c.post(URL_MCP, headers=cab, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "luna-tela", "version": "1"}}})
        if r.status_code == 401:
            raise Desconectado("token recusado")
        sid = r.headers.get("mcp-session-id")
        if sid:
            cab["Mcp-Session-Id"] = sid
        c.post(URL_MCP, headers=cab, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = c.post(URL_MCP, headers=cab, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": ferramenta, "arguments": argumentos or {}}})
        d = _dados(r)
    if "error" in d:
        raise RuntimeError(str(d["error"].get("message", d["error"]))[:200])
    partes = d.get("result", {}).get("content", [])
    return "\n".join(p.get("text", "") for p in partes if p.get("type") == "text").strip()


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(status())
    print(chamar(sys.argv[1] if len(sys.argv) > 1 else "ver_hoje")[:3000])
