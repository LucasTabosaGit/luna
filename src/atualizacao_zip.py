"""Atualizar pelo ZIP do GitHub (instalação sem git).

Baixa o ZIP do ramo main e copia os arquivos da Luna por cima. Os dados da
pessoa não estão no repositório (.env, dados/, logs/, cache/, .venv, modelos
da voz), então nunca são tocados. Exceção: atalhos.json vem no repositório,
mas é editado pela pessoa, então é mantido.

Antes de escrever, guarda uma cópia dos arquivos que vão mudar em
cache/atualizacao-backup/<versão antiga>/; se a cópia por cima falhar no
meio, restaura tudo. Arquivos que saíram do repositório são apagados só se
a Luna mesma os instalou (lista em cache/arquivos_instalados.json).
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

MANTER = {"atalhos.json"}                      # vêm no ZIP, mas são da pessoa
LIMITE_BYTES = 80 * 2**20                      # o ZIP tem ~1 MB; algo muito maior é erro


def _baixar(repo: str) -> bytes:
    url = "https://codeload.github.com/%s/zip/refs/heads/main" % repo
    req = urllib.request.Request(url, headers={"User-Agent": "Luna-atualizacao"})
    with urllib.request.urlopen(req, timeout=60) as r:
        dados = r.read(LIMITE_BYTES + 1)
    if len(dados) > LIMITE_BYTES:
        raise ValueError("arquivo grande demais")
    return dados


def _arquivos_do_zip(dados: bytes) -> dict[str, bytes]:
    """{caminho relativo: conteúdo}, sem a pasta raiz 'luna-main/'."""
    saida = {}
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            partes = info.filename.split("/", 1)
            if len(partes) < 2 or not partes[1]:
                continue
            rel = partes[1]
            # Nada de caminho absoluto nem "..": tudo fica dentro da pasta da Luna.
            if rel.startswith("/") or ".." in rel.split("/") or ":" in rel:
                raise ValueError("caminho suspeito no ZIP: %s" % rel[:80])
            saida[rel] = z.read(info)
    return saida


def aplicar(raiz: Path, repo: str, versao_atual: str, dados: bytes | None = None) -> dict:
    """Atualiza a pasta `raiz`. `dados` = ZIP já baixado (para testes)."""
    raiz = raiz.resolve()
    try:
        arquivos = _arquivos_do_zip(dados if dados is not None else _baixar(repo))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": "não deu para baixar a versão nova (%s)" % str(e)[:100]}
    try:
        nova = json.loads(arquivos["recursos/versao.json"].decode("utf-8"))
    except (KeyError, ValueError):
        return {"ok": False, "msg": "o arquivo baixado não parece ser da Luna"}
    if nova.get("repo") != repo or "src/servidor.py" not in arquivos:
        return {"ok": False, "msg": "o arquivo baixado não parece ser da Luna"}

    mudar = {}
    for rel, conteudo in arquivos.items():
        if rel in MANTER and (raiz / rel).exists():
            continue
        destino = (raiz / rel).resolve()
        if raiz not in destino.parents:
            return {"ok": False, "msg": "caminho fora da pasta da Luna: %s" % rel[:80]}
        if not destino.exists() or destino.read_bytes() != conteudo:
            mudar[rel] = conteudo

    lista_arq = raiz / "cache" / "arquivos_instalados.json"
    try:
        antes = set(json.loads(lista_arq.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        antes = set()
    sairam = sorted(r for r in antes - set(arquivos) if r not in MANTER and (raiz / r).is_file())

    backup = raiz / "cache" / "atualizacao-backup" / (versao_atual or "anterior")
    for rel in list(mudar) + sairam:
        if (raiz / rel).is_file():
            alvo = backup / rel
            alvo.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raiz / rel, alvo)

    feitos = []
    try:
        for rel, conteudo in mudar.items():
            destino = raiz / rel
            destino.parent.mkdir(parents=True, exist_ok=True)
            tmp = destino.with_name(destino.name + ".luna-novo")
            tmp.write_bytes(conteudo)
            tmp.replace(destino)
            feitos.append(rel)
        for rel in sairam:
            (raiz / rel).unlink()
    except OSError as e:
        for rel in feitos:                      # volta ao que era
            velho = backup / rel
            if velho.is_file():
                shutil.copy2(velho, raiz / rel)
            else:
                (raiz / rel).unlink(missing_ok=True)
        return {"ok": False, "msg": "não deu para trocar os arquivos (%s); nada mudou" % str(e)[:100]}

    lista_arq.parent.mkdir(parents=True, exist_ok=True)
    lista_arq.write_text(json.dumps(sorted(arquivos)), encoding="utf-8")

    if "requirements.txt" in mudar:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                            str(raiz / "requirements.txt")], cwd=str(raiz),
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            return {"ok": False, "msg": "atualizou os arquivos, mas falhou ao instalar dependências"}
    return {"ok": True, "msg": "atualizado (%d arquivo%s)" % (len(mudar), "" if len(mudar) == 1 else "s"),
            "reiniciar": True, "mudou": len(mudar), "apagou": len(sairam)}
