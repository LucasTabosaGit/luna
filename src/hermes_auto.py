"""Liga a Luna ao Hermes sozinha: cria o perfil, a senha da API e sobe o gateway.

Antes a pessoa tinha que achar o .env do perfil, inventar uma senha, escrever
API_SERVER_ENABLED/API_SERVER_KEY e colar a mesma senha na Luna. Aqui:

1. confere se o Hermes está instalado;
2. cria o perfil `assistente` se ele não existir;
3. no perfil principal (dono da porta 8642): API_SERVER_ENABLED=true e uma
   API_SERVER_KEY, se faltar;
4. no perfil `assistente`: uma API_SERVER_KEY própria, se faltar. Com vários
   perfis num gateway só, /p/assistente/v1 aceita SÓ a chave desse perfil;
5. liga o gateway (ou reinicia, se algo mudou e ele já estava no ar).

A Luna lê a chave do .env do perfil (config.chave_hermes), então ela nunca
passa pela tela nem pelos logs. Senha existente nunca é trocada: outros
programas podem estar usando.
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
from pathlib import Path

import config


def _ler(arq: Path) -> list[str]:
    try:
        return arq.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _tem(linhas: list[str], var: str) -> str:
    """Valor da variável no .env ("" se falta ou vazia), sem aspas."""
    for linha in linhas:
        m = re.match(r"\s*%s\s*=(.*)$" % re.escape(var), linha)
        if m:
            return m.group(1).strip().strip("'\"")
    return ""


def _por(arq: Path, var: str, valor: str) -> None:
    """Troca ou acrescenta VAR=valor, preservando o resto do arquivo."""
    linhas = _ler(arq)
    feito = False
    for i, linha in enumerate(linhas):
        if re.match(r"\s*%s\s*=" % re.escape(var), linha):
            linhas[i] = "%s=%s" % (var, valor)
            feito = True
    if not feito:
        linhas.append("%s=%s" % (var, valor))
    arq.parent.mkdir(parents=True, exist_ok=True)
    tmp = arq.with_suffix(".luna-tmp")
    tmp.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    os.replace(tmp, arq)


def _criar_perfil(base: Path) -> tuple[bool, str]:
    import conexoes
    import plataforma
    py = conexoes._hermes_py()
    if not py:
        return False, "Hermes não encontrado neste PC"
    cod = ("import sys\nsys.argv = ['hermes'] + sys.argv[1:]\n"
           "from hermes_cli.main import main\nmain()\n")
    env = dict(os.environ, HERMES_HOME=str(base), PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run([str(py), "-c", cod, "profile", "create", config.HERMES_PERFIL],
                           input="", capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120, env=env, **plataforma.sem_janela())
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, "não deu para criar o perfil: %s" % str(e)[:100]
    if not (base / "profiles" / config.HERMES_PERFIL).is_dir():
        return False, "não deu para criar o perfil: %s" % (r.stdout + r.stderr).strip()[-150:]
    return True, ""


def preparar(base: Path | None = None) -> dict:
    """Deixa os arquivos do Hermes prontos. Não liga nada.
    Devolve {"ok", "msg", "mudou", "passos"}."""
    import plataforma
    base = base or plataforma.hermes_home()
    if not plataforma.hermes_exe() and not (base / "hermes-agent").is_dir():
        return {"ok": False, "mudou": False, "passos": [],
                "msg": "Instale o Hermes primeiro (hermes-agent.nousresearch.com) e tente de novo."}
    passos, mudou = [], False
    perfil = base / "profiles" / config.HERMES_PERFIL
    if not perfil.is_dir():
        ok, erro = _criar_perfil(base)
        if not ok:
            return {"ok": False, "msg": erro, "mudou": False, "passos": passos}
        passos.append("perfil “%s” criado" % config.HERMES_PERFIL)
        mudou = True

    principal = base / ".env"
    linhas = _ler(principal)
    if _tem(linhas, "API_SERVER_ENABLED").lower() not in ("true", "1", "yes"):
        _por(principal, "API_SERVER_ENABLED", "true")
        passos.append("API do Hermes ligada")
        mudou = True
    if not _tem(_ler(principal), "API_SERVER_KEY"):
        _por(principal, "API_SERVER_KEY", secrets.token_urlsafe(32))
        passos.append("senha da API principal criada")
        mudou = True

    env_perfil = perfil / ".env"
    if not _tem(_ler(env_perfil), "API_SERVER_KEY"):
        _por(env_perfil, "API_SERVER_KEY", secrets.token_urlsafe(32))
        passos.append("senha do perfil “%s” criada" % config.HERMES_PERFIL)
        mudou = True
    return {"ok": True, "msg": "", "mudou": mudou, "passos": passos}


def configurar() -> dict:
    """Prepara, liga (ou reinicia) o gateway e aponta a Luna para ele."""
    import abrir
    import conexoes
    r = preparar()
    if not r["ok"]:
        return r
    # Endereço padrão na Luna (apaga um endereço antigo digitado errado) e
    # nenhuma senha colada à mão: assim a Luna lê a do .env do perfil.
    conexoes.salvar({"HERMES_URL": "http://127.0.0.1:8642/p/%s/v1" % config.HERMES_PERFIL})
    conexoes._gravar_env("API_SERVER_KEY", "")
    os.environ.pop("API_SERVER_KEY", None)
    no_ar = abrir.no_ar(8642)
    if no_ar and r["mudou"]:
        abrir.reiniciar_hermes()
        r["passos"].append("Hermes reiniciado para ler as senhas")
    elif not no_ar:
        abrir.ligar_hermes()
        import time
        for _ in range(90):
            if abrir.no_ar(8642):
                break
            time.sleep(1)
        r["passos"].append("Hermes ligado")
    t = conexoes.testar("hermes")
    r["ok"], r["msg"] = t["ok"], t["msg"]
    return r
