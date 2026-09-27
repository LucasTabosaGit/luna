"""Checagem do sistema para o tutorial de primeiro uso (tela "Primeiros passos").

Cada item: {id, nome, ok, obrigatorio, detalhe, acao?, link?}. Só leitura e
rápido (< 2 s): nada aqui instala ou muda coisa alguma.
"""
from __future__ import annotations

import shutil
import sys

import config
import plataforma


def _gpu() -> tuple[bool, str]:
    g = plataforma.gpu()
    if g["tipo"] == "mlx":
        return True, "%s (%.0f GB de memória unificada)" % (g["nome"], g["total_gb"])
    if g["tipo"] == "cuda":
        return True, "%s (%.0f GB)" % (g["nome"], g["total_gb"])
    if plataforma.MAC:
        return False, "sem MLX: o Whisper local cai para o processador"
    return False, "sem placa NVIDIA com CUDA: funciona, mas a voz fica lenta"


def _hermes_instalado() -> bool:
    return plataforma.hermes_exe() is not None


def checar() -> list[dict]:
    import conexoes
    itens = []

    def item(id_, nome, ok, detalhe, obrig=False, **extra):
        itens.append({"id": id_, "nome": nome, "ok": bool(ok), "obrigatorio": obrig,
                      "detalhe": detalhe, **extra})

    v = sys.version_info
    item("python", "Python 3.11", v[:2] == (3, 11), "%d.%d.%d" % v[:3], True)
    ff = shutil.which("ffmpeg")
    item("ffmpeg", "ffmpeg", ff, "encontrado" if ff else "falta: abra um terminal e rode  winget install ffmpeg",
         True, link="https://www.gyan.dev/ffmpeg/builds/")
    ok, det = _gpu()
    motor = config.stt_id()
    if motor != "local":
        item("gpu", "Placa de vídeo", True,
             "não precisa: a fala é transcrita na nuvem (%s)" % config.STTS[motor]["nome"].split(": ")[-1])
    else:
        item("gpu", "GPU do Mac (MLX)" if plataforma.MAC else "Placa de vídeo (CUDA)", ok,
             det if ok else "sem placa NVIDIA: escolha o ouvido na nuvem (Groq tem cota grátis)",
             acao="conexoes")
    mod_w = any((config.MODELOS / "whisper").glob("**/model.bin")) if (config.MODELOS / "whisper").exists() else False
    item("modelos", "Modelos de voz", mod_w,
         "baixados" if mod_w else "baixam sozinhos na primeira vez (~3 GB); aguarde")
    import detector_luna as d
    item("detector", "Detector do nome “Luna”", d.disponivel(),
         "treinado com a sua voz" if "modelos" in str(d.CLASSIFICADOR) else "genérico (funciona para qualquer voz)")

    cx = conexoes.status(False)
    cb = cx["cerebro"]
    nome_cb = next(o["nome"] for o in cb["opcoes"] if o["id"] == cb["escolhido"])
    item("cerebro", "IA principal (cérebro)", cb["configurado"],
         ("%s configurado" % nome_cb) if cb["configurado"]
         else "escolha uma IA e cole a chave (DeepSeek, Gemini, OpenAI...)",
         True, acao="conexoes", link=cb["link"])
    herm = _hermes_instalado()
    item("hermes", "Hermes Agent (Claude)", herm and cx["hermes"]["configurado"],
         ("instalado e conectado" if herm and cx["hermes"]["configurado"]
          else "instalado; falta conectar em Conexões" if herm
          else "opcional: dá à Luna o modo Expert e o controle do PC"),
         acao="conexoes", link="https://hermes-agent.nousresearch.com/")
    try:
        import meuhoje
        mh = meuhoje.status()["conectado"]
    except Exception:  # noqa: BLE001
        mh = False
    item("meuhoje", "Meu Hoje (tarefas e agenda)", mh,
         "conta conectada" if mh else "opcional: crie a conta e conecte na aba Hoje",
         acao="hoje", link="https://meuhoje.com.br/cadastro")
    return itens


if __name__ == "__main__":
    import json
    print(json.dumps(checar(), ensure_ascii=False, indent=1))
