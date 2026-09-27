"""Servidor da interface web do assistente de voz.

Por que navegador e não uma janela nativa: o `getUserMedia` do browser
traz cancelamento de eco, supressão de ruído e ganho automático prontos
— exatamente o problema que o `silenciar()` resolve na marra no modo
CLI. Com o navegador, o microfone não escuta o alto-falante nem quando
o som está alto.

Protocolo do WebSocket:

    cliente → servidor
        binário : PCM int16, 16 kHz, mono (blocos de ~32 ms)
        texto   : {"tipo": "config", ...}

    servidor → cliente
        texto   : {"tipo": "estado"|"voce"|"bot"|"metrica", ...}
        binário : PCM int16, 24 kHz, mono (áudio da resposta)

O processamento (STT/LLM/TTS) é síncrono e roda em thread separada:
rodá-lo no laço de eventos travaria a recepção do microfone.
"""
from __future__ import annotations

import asyncio
import json
import re
import os
os.environ.setdefault("FOR_DISABLE_CONSOLE_CTRL_HANDLER", "1")   # MKL: fechar o console nao derruba
import sys
import threading
import time
from pathlib import Path

# Log redirecionado para arquivo usa cp1252 no Windows e o "→" do banner
# derrubava a subida. Força UTF-8 venha de onde vier o lançamento.
for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402  ajusta HF_HOME antes do torch

from contextlib import asynccontextmanager  # noqa: E402

import numpy as np  # noqa: E402
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

WEB = config.RAIZ / "web"

# Carregados uma vez, no arranque: cada modelo custa 10-30s para subir e
# a primeira chamada de cada um é ~15x mais lenta (kernels CUDA).
_ouvido = None
_voz = None
_vad = None
_llm = None


@asynccontextmanager
async def _ciclo(app: FastAPI):
    await asyncio.to_thread(_modelos)
    laco = asyncio.get_running_loop()
    try:
        import atalho_global
        if atalho_global.iniciar(lambda: laco.call_soon_threadsafe(
                lambda: asyncio.ensure_future(_atalho_global()))):
            print("  [atalho] %s chama a Luna" % atalho_global.DESCRICAO, flush=True)
        else:
            print("  [atalho] %s ocupado por outro programa" % atalho_global.DESCRICAO, flush=True)
    except Exception as e:  # noqa: BLE001
        print("  [atalho] indisponível: %s" % str(e)[:80], flush=True)
    yield
    try:
        import atalho_global
        atalho_global.parar()
    except Exception:  # noqa: BLE001
        pass


async def _atalho_global() -> None:
    """Ctrl+Alt+L: janela na frente e já ouvindo (como dizer "Luna")."""
    import atalho_global
    achou = await asyncio.to_thread(atalho_global.trazer_para_frente)
    print("  [atalho] %s" % ("janela na frente" if achou else "abrindo a janela"), flush=True)
    if not _TELAS:
        return
    s = list(_TELAS)[-1]
    ativ = _atualizado("ativacao")
    s.acordado_ate = time.time() + ativ.JANELA_NOME_S
    s.nome_ate = s.acordado_ate
    await s.enviar(tipo="ligar_mic")
    await s.enviar(tipo="acordado", ate=ativ.JANELA_NOME_S)
    await s.enviar(tipo="estado", estado="ouvindo")
    await s.enviar(tipo="chamada")


app = FastAPI(title="Assistente de voz local", lifespan=_ciclo)


def _modelos():
    global _ouvido, _voz, _vad, _llm
    if _ouvido is not None:
        return _ouvido, _voz, _vad, _llm

    from escuta import EscutaVAD
    from fala import Voz
    from openai import OpenAI
    import ouvido as _mod_ouvido

    print("carregando modelos...", flush=True)
    t0 = time.time()

    _ouvido = _mod_ouvido.criar()
    _ouvido.carregar()

    # Kokoro = voz de reserva (internet caiu). Com a fala na nuvem, a Luna
    # quer poupar a placa de vídeo: ele só carrega quando for preciso.
    _voz = Voz()
    if config.stt_id() == "local":
        _voz.carregar()

    _vad = EscutaVAD()
    _vad.carregar()

    import custo
    _llm = custo.embrulhar(OpenAI(base_url=config.LLM_URL or "http://127.0.0.1:9/v1",
                                  api_key=config.LLM_API_KEY or "sem-chave", timeout=30))

    import atualizacao
    atualizacao.marcar_inicio()      # "a versão que está rodando", para o aviso de reinício

    # Comandos prontos: o import dispara a leitura dos apps do Iniciar
    # em segundo plano, para o primeiro "abre X" não esperar.
    import comandos  # noqa: F401

    print("pronto em %.1fs" % (time.time() - t0), flush=True)
    return _ouvido, _voz, _vad, _llm


# ------------------------------------------------------------ cérebro rápido
def _cerebro_rapido():
    """(cliente, modelo, extra) do cérebro rápido: a IA escolhida em Conexões."""
    return _modelos()[3], config.LLM_MODELO, config.LLM_EXTRA


@app.get("/")
async def raiz():
    return FileResponse(WEB / "index.html")


@app.get("/atualizacao")
async def atualizacao_status(web: str = "", forcar: int = 0):
    """Atualizações pendentes (tela nova, reiniciar, versão nova no GitHub)."""
    import atualizacao
    return await asyncio.to_thread(atualizacao.pendentes, web, bool(forcar))


@app.post("/atualizar")
async def atualizacao_aplicar():
    """Versão pública com git: git pull + dependências. Depois a tela reinicia."""
    import atualizacao
    return await asyncio.to_thread(atualizacao.atualizar)


@app.get("/conversas")
async def conversas_lista():
    import conversas as conv
    return {"conversas": await asyncio.to_thread(conv.listar)}


@app.delete("/conversas/{cid}")
async def conversas_apagar(cid: str):
    import conversas as conv
    return {"ok": await asyncio.to_thread(conv.apagar, cid)}


@app.get("/conversas/img/{nome}")
async def conversas_img(nome: str):
    import conversas as conv
    if not re.fullmatch(r"[a-f0-9]{16}\.(jpg|png|webp)", nome):
        return JSONResponse({"erro": "nome inválido"}, status_code=400)
    arq = conv.PASTA_IMG / nome
    if not arq.exists():
        return JSONResponse({"erro": "não existe"}, status_code=404)
    return FileResponse(arq, headers={"Cache-Control": "max-age=31536000, immutable"})


@app.get("/aprendizado")
async def aprendizado_ver():
    import aprendizado
    return await asyncio.to_thread(aprendizado.resumo)


@app.get("/treino-voz")
async def treino_voz_status():
    """Detector do nome com a voz de quem usa (src/treino_voz.py)."""
    import treino_voz
    return await asyncio.to_thread(treino_voz.status)


@app.post("/treino-voz/{acao}")
async def treino_voz_acao(acao: str):
    import treino_voz
    if acao == "treinar":
        if not treino_voz.status()["rodando"]:
            asyncio.get_running_loop().run_in_executor(None, treino_voz.treinar)
        return {"ok": True}
    feito = {"aprovar": treino_voz.aprovar, "descartar": treino_voz.descartar,
             "desfazer": treino_voz.desfazer}.get(acao)
    if feito is None:
        return JSONResponse({"erro": "ação desconhecida"}, status_code=404)
    r = await asyncio.to_thread(feito)
    return {"ok": r is not False}


@app.post("/aprendizado/atalho/{nome}/{estado}")
async def aprendizado_atalho(nome: str, estado: str):
    import aprendizado
    await asyncio.to_thread(aprendizado.ligar_atalho, nome, estado == "ligar")
    return {"ok": True}


_hoje_cache: dict = {"t": 0.0, "dados": None}


def _rotina():
    """Aba Hoje: o Meu Hoje (src/meuhoje.py)."""
    import meuhoje
    return meuhoje


# ------------------------------------------------------------ RVC sob demanda
# O serviço RVC (vozes convertidas) é opcional e pesado (~0,7 GB de VRAM,
# ~700 MB de RAM, ~20 s para ligar). Ele NÃO sobe com a Luna: liga quando
# alguém escolhe uma voz convertida e desliga quando troca para outra.
_rvc_lock = threading.Lock()


def _rvc_no_ar() -> bool:
    import socket
    with socket.socket() as sk:
        sk.settimeout(0.3)
        return sk.connect_ex(("127.0.0.1", 8778)) == 0


def _rvc_ligar(esperar_s: float = 90) -> bool:
    """Sobe o serviço se preciso e espera o /saude responder."""
    import subprocess
    import urllib.request
    if not config.rvc_instalado():
        return False
    with _rvc_lock:
        if not _rvc_no_ar():
            py = config.RAIZ / ".venv-rvc" / "Scripts" / "python.exe"
            logs = config.RAIZ / "logs"
            logs.mkdir(exist_ok=True)
            print("  [rvc] ligando o serviço (voz convertida escolhida)...", flush=True)
            subprocess.Popen([str(py), str(config.RAIZ / "src" / "rvc_servico.py")], cwd=str(config.RAIZ),
                             stdout=open(logs / "rvc.log", "ab"), stderr=open(logs / "rvc.err.log", "ab"),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        fim = time.time() + esperar_s
        while time.time() < fim:
            try:
                urllib.request.urlopen(config.RVC_URL + "/saude", timeout=2).read()
                return True
            except Exception:  # noqa: BLE001
                time.sleep(1)
    return False


def _rvc_desligar() -> None:
    try:
        import psutil
        for c in psutil.net_connections("tcp"):
            if c.laddr and c.laddr.port == 8778 and c.status == "LISTEN" and c.pid:
                psutil.Process(c.pid).terminate()
                print("  [rvc] desligado (nenhuma voz convertida em uso)", flush=True)
    except Exception:  # noqa: BLE001
        pass


@app.post("/desligar")
async def desligar():
    """Desliga a Luna (servidor e vozes RVC). O Hermes fica: é de outros usos."""
    await asyncio.to_thread(_rvc_desligar)
    asyncio.get_running_loop().call_later(0.8, os._exit, 0)
    return {"ok": True}


@app.get("/sistema")
async def sistema_checar():
    """Primeiros passos: o que está instalado e conectado."""
    import sistema
    return await asyncio.to_thread(sistema.checar)


@app.get("/meuhoje/status")
async def meuhoje_status():
    import meuhoje
    return await asyncio.to_thread(meuhoje.status)


@app.post("/meuhoje/conectar")
async def meuhoje_conectar(req: Request):
    """Devolve a URL de login do Meu Hoje (a tela abre numa aba nova)."""
    import meuhoje
    volta = str(req.base_url).rstrip("/") + "/meuhoje/retorno"
    try:
        url = await asyncio.to_thread(meuhoje.iniciar_login, volta)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "msg": str(e)[:200]}, status_code=502)
    return {"ok": True, "url": url}


@app.get("/meuhoje/retorno")
async def meuhoje_retorno(code: str = "", state: str = "", error: str = ""):
    """Volta do login: troca o código pelo token e fecha a aba."""
    from fastapi.responses import HTMLResponse
    import meuhoje
    msg, ok = "Conta conectada. Pode fechar esta aba.", True
    if error or not code:
        msg, ok = "Login cancelado (%s)." % (error or "sem código"), False
    else:
        try:
            await asyncio.to_thread(meuhoje.concluir_login, code, state)
            _hoje_cache["t"] = 0.0
        except Exception as e:  # noqa: BLE001
            msg, ok = "Não deu certo: %s" % str(e)[:160], False
    cor = "#6fd3b8" if ok else "#ef7d6b"
    html = ("<!doctype html><meta charset=utf-8><title>Luna · Meu Hoje</title>"
            "<body style='background:#1b1a18;color:#eee;font:16px system-ui;display:grid;"
            "place-items:center;height:100vh;margin:0'><div style='text-align:center'>"
            "<div style='font-size:40px;color:%s'>%s</div><p>%s</p></div>"
            "<script>try{window.opener&&window.opener.postMessage('meuhoje-ok','*')}catch(e){}"
            "%s</script>") % (cor, "✓" if ok else "✕", msg,
                              "setTimeout(()=>window.close(),1500)" if ok else "")
    return HTMLResponse(html)


@app.post("/meuhoje/desconectar")
async def meuhoje_desconectar():
    import meuhoje
    await asyncio.to_thread(meuhoje.desconectar)
    _hoje_cache.update(t=0.0, dados=None)
    return {"ok": True}


def _hoje_ler() -> dict:
    """ver_hoje + agenda dos próximos 7 dias, do plugin de rotina."""
    import datetime

    rot = _rotina()
    hoje = datetime.date.today()
    dia = json.loads(rot.chamar("ver_hoje") or "{}")
    ag = json.loads(rot.chamar("listar_agenda", {
        "de": hoje.isoformat(), "ate": (hoje + datetime.timedelta(days=7)).isoformat()}) or "{}")
    dia["proximos"] = ag.get("agendamentos", [])
    return dia


@app.get("/hoje")
async def hoje_ver(forcar: int = 0):
    rot = _rotina()
    if not forcar and _hoje_cache["dados"] and time.time() - _hoje_cache["t"] < 60:
        return _hoje_cache["dados"]
    try:
        dados = await asyncio.to_thread(_hoje_ler)
    except rot.Desconectado as e:
        return JSONResponse({"erro": "desconectado", "detalhe": str(e)}, status_code=401)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"erro": "falhou", "detalhe": str(e)[:160]}, status_code=502)
    _hoje_cache.update(t=time.time(), dados=dados)
    return dados


@app.post("/hoje/{acao}")
async def hoje_acao(acao: str, corpo: dict):
    """Concluir / adiar / criar tarefa direto da aba Hoje (sem o Claude)."""
    rot = _rotina()
    ferramentas = {"concluir": "concluir_tarefa", "adiar": "adiar_tarefa", "criar": "criar_tarefa"}
    if acao not in ferramentas:
        return JSONResponse({"erro": "ação inválida"}, status_code=400)
    permitidos = {"concluir": {"id"}, "adiar": {"id", "ate"}, "criar": {"titulo", "prazo", "planejadaPara"}}
    args = {k: v for k, v in (corpo or {}).items() if k in permitidos[acao] and v}
    try:
        r = await asyncio.to_thread(rot.chamar, ferramentas[acao], args)
    except rot.Desconectado as e:
        return JSONResponse({"erro": "desconectado", "detalhe": str(e)}, status_code=401)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"erro": "falhou", "detalhe": str(e)[:160]}, status_code=502)
    _hoje_cache["t"] = 0.0
    return {"ok": True, "resposta": r[:400]}


@app.get("/vozes")
async def vozes():
    """Lista as vozes para a interface montar o seletor antes de conectar."""
    return {"vozes": config.vozes_disponiveis(), "padrao": config.TTS_VOZ,
            "velocidade": config.TTS_VELOCIDADE}


@app.get("/saude")
async def saude():
    ouvido, _voz, _vad, llm = _modelos()
    # "carregado" = servidor pronto (Whisper, vozes). O DeepSeek é checado à
    # parte: sem internet a Luna ainda ouve e roda os comandos prontos.
    try:
        modelos = [m.id for m in llm.models.list().data]
        llm_ok = True
    except Exception as e:  # noqa: BLE001
        modelos, llm_ok = [str(e)[:120]], False
    return {
        "stt": {"dispositivo": ouvido.dispositivo, "compute": ouvido.compute},
        "llm": {"modelo": config.LLM_MODELO, "carregado": True, "deepseek_ok": llm_ok,
                "disponiveis": modelos[:20]},
        "tts": {"voz": config.TTS_VOZ},
        "hermes": {"online": await asyncio.to_thread(_hermes_online)},
    }


def _hermes_online() -> bool:
    """O API server do Hermes responde? (gateway pode ter caído)"""
    import urllib.request

    try:
        req = urllib.request.Request(
            config.HERMES_URL + "/models",
            headers={"Authorization": "Bearer %s" % config.chave_hermes()})
        urllib.request.urlopen(req, timeout=3).read()
        return True
    except Exception:  # noqa: BLE001
        return False


@app.post("/reiniciar")
async def reiniciar():
    """Reinicia o próprio servidor: lança um substituto e sai.

    Chamado pelo reiniciar.ps1. Quem pede costuma ser o Hermes, dentro de
    uma conversa que passa por ESTE servidor; se o script derrubasse a
    porta e tentasse religar, morria junto com a conversa (o Hermes
    interrompe a ferramenta) e ninguém religava. Aqui o substituto nasce
    deste processo, fora da árvore do Hermes, e espera este sair.
    """
    import subprocess

    raiz = Path(__file__).resolve().parent.parent
    env = dict(os.environ, REINICIO_ESPERA_PID=str(os.getpid()))
    log = open(raiz / "logs" / "srv.log", "ab")
    err = open(raiz / "logs" / "srv.err.log", "ab")
    flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | NEW_PROCESS_GROUP
    try:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=str(raiz),
                         env=env, stdout=log, stderr=err, stdin=subprocess.DEVNULL,
                         creationflags=flags | 0x01000000)   # + BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=str(raiz),
                         env=env, stdout=log, stderr=err, stdin=subprocess.DEVNULL,
                         creationflags=flags)
    print("  [reiniciar] substituto lançado; saindo em 1s", flush=True)
    # Sai depois de responder, para quem chamou receber o OK.
    asyncio.get_running_loop().call_later(1.0, os._exit, 0)
    return {"ok": True, "volta_em_s": 30}


@app.get("/conexoes")
async def conexoes_status(testar: int = 0):
    """Ajustes → Conexões: o que está configurado (chaves mascaradas)."""
    import conexoes
    return await asyncio.to_thread(conexoes.status, bool(testar))


@app.post("/conexoes")
async def conexoes_salvar(req: Request):
    """Grava chaves no .env e testa na hora. Vale sem reiniciar."""
    global _llm
    import conexoes
    corpo = await req.json()
    nome = corpo.get("servico", "")
    try:
        conexoes.salvar(corpo.get("valores") or {})
    except ValueError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)
    if nome == "cerebro" and _llm is not None:
        import custo
        from openai import OpenAI
        _llm = custo.embrulhar(OpenAI(base_url=config.LLM_URL or "http://127.0.0.1:9/v1",
                                      api_key=config.LLM_API_KEY or "sem-chave", timeout=30))
    if nome == "hermes":
        _hermes_cache[0] = 0.0
    if nome == "stt" and _ouvido is not None:
        await asyncio.to_thread(_trocar_stt)
    return await asyncio.to_thread(conexoes.testar, nome)


def _trocar_stt() -> None:
    """Troca onde a fala vira texto sem reiniciar. Saindo do Whisper local,
    devolve a memória da placa de vídeo."""
    global _ouvido
    import ouvido as _mod_ouvido
    atual = getattr(_ouvido, "motor", "local")
    if atual == config.stt_id():
        return
    novo = _mod_ouvido.criar()
    novo.carregar()                 # local: ~5 s para carregar o Whisper
    velho, _ouvido = _ouvido, novo
    if hasattr(velho, "descarregar"):
        velho.descarregar()
    del velho
    import gc
    gc.collect()
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass
    print("  [stt] agora: %s" % config.STTS[config.stt_id()]["nome"], flush=True)


def _uso_recursos() -> dict:
    """Medições para Ajustes -> Requisitos. Só leitura, rápido."""
    import psutil
    r = {"ram_luna_mb": psutil.Process().memory_info().rss // 2**20,
         "ram_total_gb": round(psutil.virtual_memory().total / 2**30),
         "ram_livre_gb": round(psutil.virtual_memory().available / 2**30, 1),
         "disco_livre_gb": round(psutil.disk_usage(str(config.RAIZ)).free / 2**30),
         "gpu": "", "vram_total_gb": 0, "vram_usada_gb": 0}
    try:
        import torch
        if torch.cuda.is_available():
            livre, total = torch.cuda.mem_get_info()
            r.update(gpu=torch.cuda.get_device_name(0), vram_total_gb=round(total / 2**30, 1),
                     vram_usada_gb=round((total - livre) / 2**30, 1))
    except Exception:  # noqa: BLE001
        pass
    # O que da Luna está ocupando a placa agora (medido nesta máquina).
    partes = []
    if _ouvido is not None and getattr(_ouvido, "dispositivo", "") == "cuda":
        partes.append({"nome": "Whisper (ouvido na placa)", "gb": 2.2})
    if _voz is not None and getattr(_voz, "_pipe", None) is not None:
        partes.append({"nome": "Kokoro (voz de reserva)", "gb": 0.6})
    if _rvc_no_ar():
        partes.append({"nome": "RVC (voz convertida)", "gb": 0.7})
    r["partes"] = partes
    r["vram_luna_gb"] = round(sum(p["gb"] for p in partes), 1)
    return r


def _dicas(rec: dict, est: dict) -> list[dict]:
    """Dicas de 'mais leve' e 'mais barato' para a configuração ATUAL."""
    d = []
    stt = config.stt_id()
    tem_gpu = bool(rec["gpu"])
    if stt == "local" and tem_gpu:
        d.append({"tipo": "leve", "titulo": "Liberar a placa de vídeo",
                  "texto": "O ouvido na placa usa ~2,2 GB. Na nuvem (Groq, cota grátis) a placa fica livre "
                           "para jogos e outros programas, com a mesma qualidade.", "aba": "ia"})
    if stt == "local" and not tem_gpu:
        d.append({"tipo": "leve", "titulo": "Sem placa NVIDIA: use o ouvido na nuvem",
                  "texto": "No processador o Whisper fica lento (vários segundos por frase). "
                           "Groq tem cota grátis e responde em ~0,4 s.", "aba": "ia"})
    if stt != "local" and tem_gpu and rec["vram_total_gb"] >= 4:
        d.append({"tipo": "leve", "titulo": "Você tem placa: o ouvido pode ser grátis",
                  "texto": "Com %s, o Whisper local é grátis e funciona sem internet." % rec["gpu"], "aba": "ia"})
    if _rvc_no_ar():
        d.append({"tipo": "leve", "titulo": "Voz convertida ligada (+0,7 GB)",
                  "texto": "As vozes da Microsoft e o JARVIS não usam a placa e soam naturais.", "aba": "voz"})
    ias = est.get("ias", [])
    atual = config.cerebro_id()
    custo_atual = next((i["brl_mes"] for i in ias if i["id"] == atual), None)
    pagas = [i for i in ias if not i["gratis"] and i["id"] != atual]
    # Só sugere trocar se a economia for de verdade (>= R$ 5/mês): trocar de
    # IA para economizar centavos piora a Luna à toa.
    def _r(v):
        return ("R$ %.2f" % v).replace(".", ",")
    if custo_atual is not None and pagas and custo_atual - pagas[0]["brl_mes"] >= 5:
        d.append({"tipo": "barato", "titulo": "Dá para pagar menos na IA",
                  "texto": "Com o seu uso, %s custaria ~%s/mês (hoje: ~%s)."
                           % (pagas[0]["nome"], _r(pagas[0]["brl_mes"]), _r(custo_atual)), "aba": "ia"})
    elif custo_atual is not None and custo_atual < 5 and atual not in ("ollama", "assinatura"):
        d.append({"tipo": "barato", "titulo": "Seu gasto já é baixo",
                  "texto": "No seu ritmo, a IA principal sai por ~%s/mês. Trocar de IA quase não "
                           "mudaria a conta." % _r(custo_atual), "aba": ""})
    if atual not in ("gemini", "groq", "ollama", "assinatura"):
        d.append({"tipo": "barato", "titulo": "Opções sem custo",
                  "texto": "Gemini e Groq têm cota grátis; Ollama roda no seu PC (grátis, porém mais fraco "
                           "e usa a placa); ou use a sua assinatura do ChatGPT pelo Hermes.", "aba": "ia"})
    d.append({"tipo": "barato", "titulo": "O modo Expert não gasta a IA principal",
              "texto": "Ele usa o Claude pelo Hermes, ou seja, a sua assinatura. Mas é mais lento: "
                       "deixe no Auto para o dia a dia.", "aba": ""})
    return d


@app.get("/uso")
async def uso():
    """Ajustes -> Uso e Requisitos: gasto, histórico, medições e dicas."""
    import custo

    def _tudo():
        rec = _uso_recursos()
        est = custo.estimativas()
        return {"hoje": custo.hoje(), "historico": custo.historico(30), "estimativas": est,
                "recursos": rec, "dicas": _dicas(rec, est),
                "cerebro": config.LLM_NOME, "cerebro_id": config.cerebro_id(),
                "stt": config.STTS[config.stt_id()]["nome"], "stt_id": config.stt_id()}
    return await asyncio.to_thread(_tudo)


@app.get("/estado")
async def estado_rapido():
    """Checagem leve para a tela: o que está no ar agora."""
    import custo
    return {"hermes": await asyncio.to_thread(_hermes_online),
            "modelo": config.LLM_MODELO,
            "modelos_claude": config.modelos_expert(),
            "gemini": bool(config.chave_gemini()),
            "cerebro": bool(config.chave_cerebro()) and bool(config.LLM_URL),
            "cerebro_nome": config.LLM_NOME,

            "custo": await asyncio.to_thread(custo.hoje)}


# ------------------------------------------------ propostas (trava de aprovação)
# As telas abertas, para avisar quando uma proposta chega.
_TELAS: dict = {}   # sessões abertas, em ordem de chegada (dict = set ordenado)

# ------------------------------------------------------------------ modo mini
# O globo (src/mini.py) é uma janelinha à parte: não tem microfone nem
# áudio, só espelha o estado da janela principal e manda os cliques.
_ESPELHOS: set = set()
_ESPELHAR = {"estado", "acordado", "interrompido", "bot", "voce", "aviso", "ferramenta"}
_ultimo_estado = {"tipo": "estado", "estado": "desligado"}


async def _espelhar(dados: dict) -> None:
    global _ultimo_estado
    if dados.get("tipo") == "estado":
        _ultimo_estado = dict(dados)
    if dados.get("tipo") in ("voce", "bot", "aviso", "ferramenta"):
        dados = {k: dados[k] for k in ("tipo", "texto", "ferramenta", "status", "parcial")
                 if k in dados}          # só o que a legenda usa
    for w in list(_ESPELHOS):
        try:
            await w.send_text(json.dumps(dados, ensure_ascii=False))
        except Exception:  # noqa: BLE001 - globo fechado
            _ESPELHOS.discard(w)


@app.post("/mini")
async def abrir_mini():
    """Abre o globo e minimiza a janela principal (feito pelo mini.py)."""
    import subprocess
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    subprocess.Popen([str(pyw if pyw.exists() else py), str(Path(__file__).with_name("mini.py"))],
                     cwd=str(config.RAIZ), creationflags=0x08000000)   # sem console
    return {"ok": True}


@app.websocket("/ws_espelho")
async def ws_espelho(ws: WebSocket):
    await ws.accept()
    _ESPELHOS.add(ws)
    try:
        await ws.send_text(json.dumps(_ultimo_estado, ensure_ascii=False))
        while True:
            cmd = json.loads(await ws.receive_text())
            if cmd.get("tipo") != "clique" or not _TELAS:
                continue

            def _ocupada(x):
                return x.tocando or (x.tarefa is not None and not x.tarefa.done())
            # Com mais de uma janela aberta: a que está falando/pensando
            # (para parar); senão a mais recente.
            telas = list(_TELAS)
            s = next((x for x in telas if _ocupada(x)), telas[-1])
            ocupada = _ocupada(s)
            if ocupada:
                # Falando/pensando: um clique para tudo (igual "cancela").
                print("  [mini] clique: parar", flush=True)
                await _barge_in(s)
            else:
                # Parada: acorda para ouvir, como dizer "Luna".
                print("  [mini] clique: ouvir", flush=True)
                ativ = _atualizado("ativacao")
                s.acordado_ate = time.time() + ativ.JANELA_NOME_S
                await s.enviar(tipo="ligar_mic")
                await s.enviar(tipo="acordado", ate=ativ.JANELA_NOME_S)
                await s.enviar(tipo="estado", estado="ouvindo")
    except Exception:  # noqa: BLE001 - desconectou
        pass
    finally:
        _ESPELHOS.discard(ws)


def _propostas():
    import importlib
    return importlib.import_module("propostas")


async def _avisar_telas(**msg) -> None:
    for s in list(_TELAS):
        try:
            await s.enviar(**msg)
        except Exception:  # noqa: BLE001 - aba fechada
            _TELAS.pop(s, None)


@app.post("/propostas/aviso")
async def proposta_aviso(ev: dict):
    """propostas.py avisa: chegou uma proposta (ou foi recusada nos testes)."""
    print("  [proposta] %s %s: %s" % (ev.get("id"), ev.get("estado"), ev.get("titulo")),
          flush=True)
    await _avisar_telas(tipo="proposta", **{k: ev.get(k) for k in
                        ("id", "estado", "titulo", "arquivos", "testes", "motivos")})
    return {"ok": True}


@app.get("/propostas")
async def propostas_pendentes():
    return {"pendentes": await asyncio.to_thread(lambda: _propostas().pendentes())}


@app.post("/propostas/{pid}/{decisao}")
async def proposta_decidir(pid: str, decisao: str):
    """Só a TELA chama isto (botões Aprovar/Recusar). O Hermes não aprova."""
    if decisao not in ("aprovar", "recusar"):
        return {"ok": False, "texto": "decisão inválida"}
    p = _propostas()
    texto = await asyncio.to_thread(p.aprovar if decisao == "aprovar" else p.recusar, pid)
    if decisao == "aprovar" and texto.startswith("Aplicada"):
        # Força recarregar tudo na próxima frase (o commit mudou os arquivos).
        # Não pode ser .clear(): sem a versão antiga, o mtime novo seria
        # tomado como "já visto" e a mudança aprovada nunca carregaria.
        for k in list(_versao_mod):
            _versao_mod[k] = 0.0
    print("  [proposta] %s %s: %s" % (pid, decisao, texto), flush=True)
    await _avisar_telas(tipo="proposta", id=pid, estado=decisao + "da", texto=texto)
    return {"ok": True, "texto": texto}


# Quanto áudio de antes do início detectado entra na frase (300 ms).
PRE_FALA = int(0.3 * config.SAMPLE_RATE)


class Sessao:
    """Estado de uma conversa: buffer do microfone, VAD e histórico."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.buf: list[np.ndarray] = []
        self.falando = False
        self.mudo_amostras = 0     # silêncio contado em amostras, não em segundos
        self.ocupado = False          # ignora o microfone enquanto responde
        self.historico = [{"role": "system", "content": config.SISTEMA}]
        # Conversa salva em dados/conversas (sobrevive a recarregar a tela).
        import conversas as _conv
        self.conversa = _conv.novo_id()
        self.fotos_pendentes: list[str] = []   # imagens do próximo "você"
        self.resto = np.zeros(0, dtype=np.float32)
        self.pre: list = []        # áudio logo antes do início da fala
        # Fala que chegou enquanto um turno rodava. Usada quando o turno
        # era só o nome ("Luna" ... "pausa o Spotify" com respiro no meio).
        self.pendente = None
        self.so_nome = False
        # FILA: pedidos feitos enquanto ela trabalha (falados ou digitados).
        # Rodam um por vez, na ordem; "cancela" esvazia. Antes o falado era
        # descartado e o digitado CANCELAVA o que estava rodando - foi assim
        # que "já terminou?" matou a proposta do seletor pela metade.
        self.fila: list[dict] = []
        # Último print olhado: (data_url, quando, pergunta) - para "pensa melhor".
        self.ultima_tela = None
        # Preferências desta aba: trocar a voz não afeta outras sessões
        # nem exige reiniciar o servidor.
        self.voz = config.TTS_VOZ
        self.velocidade = config.TTS_VELOCIDADE
        # Cérebro: "auto" (juiz escolhe a cada frase), "rapido" (LM Studio)
        # ou "hermes" (agente com ferramentas, Claude).
        self.cerebro = "auto"
        # Modelo do Claude para o cérebro Hermes ("" = padrão do perfil).
        # Por sessão, como a voz: trocar numa aba não afeta as outras.
        self.modelo_claude = (config.HERMES_MODELO_PADRAO
                              if config.modelo_claude_valido(
                                  config.HERMES_MODELO_PADRAO) else "")
        # Turno em andamento, para poder interromper (Esc / botão).
        self.tarefa: asyncio.Task | None = None
        self.cancelar = threading.Event()
        # Timers dos comandos prontos ("me avisa em 5 minutos").
        self.timers: list[asyncio.Task] = []
        # Modo Live: áudio nativo com o Gemini, sem STT/TTS locais.
        self.live = None
        self.live_tarefa = None
        # Palavra de ativação "Hermes": com ela ligada, só responde falas
        # que chamam pelo nome, ou dentro da janela de conversa.
        self.ativacao = False
        self.acordado_ate = 0.0
        # Conversa contínua (emendar pedidos sem repetir "Luna"). Desligada
        # por padrão: o som da caixa entrava como pedido. Só o nome sozinho
        # ("Luna." e o pedido logo depois) abre uma janela curta.
        self.continua = False
        self.nome_ate = 0.0
        # Quando começou a fala atual: vale estar dentro da janela ao
        # COMEÇAR a falar (uma frase longa termina depois dos 5 s).
        self.t_inicio_fala = 0.0
        # Interromper falando por cima ("barge-in"). `tocando` vem do
        # navegador: há áudio da resposta saindo na caixa de som agora.
        self.tocando = False
        self.barge = True
        self.barge_resto = np.zeros(0, dtype=np.float32)
        self.barge_fala = 0          # amostras de fala recente
        self.barge_buf: list[np.ndarray] = []   # começo da sua fala, reaproveitado

    # ------------------------------------------------------------------
    def detectar_barge(self, pcm: np.ndarray) -> bool:
        """Você começou a falar por cima da resposta?

        Exige fala FORTE (Silero >= 0,7) somando BARGE_MIN_S, com decaimento
        no silêncio: um estalo, tosse ou resto de eco que o cancelamento do
        navegador deixou passar não chega lá; uma frase chega em ~0,4 s.
        """
        _o, _v, vad, _l = _modelos()
        self.barge_buf.append(pcm)
        # guarda ~1,5 s: o começo da frase que disparou vira o pedido
        while sum(len(b) for b in self.barge_buf) > 24000:
            self.barge_buf.pop(0)
        dados = np.concatenate([self.barge_resto, pcm]) if len(self.barge_resto) else pcm
        n = (len(dados) // 512) * 512
        self.barge_resto = dados[n:].copy()
        for i in range(0, n, 512):
            p = vad._prob_fala(dados[i:i + 512])
            if p >= 0.7:
                self.barge_fala += 512
            else:
                self.barge_fala = max(0, self.barge_fala - 256)
        return self.barge_fala >= config.BARGE_MIN_S * config.SAMPLE_RATE

    def zerar_barge(self) -> None:
        self.barge_resto = np.zeros(0, dtype=np.float32)
        self.barge_fala = 0
        self.barge_buf = []
    # ------------------------------------------------------------------
    async def enviar(self, **dados) -> None:
        if dados.get("tipo") == "bot" and dados.get("texto"):
            # Guarda o que ela mesma disse: o eco disso no microfone não
            # pode ser "resgatado" como chamada ("Vou ficar atenta.").
            ditas = getattr(self, "ditas", [])
            ditas.append((time.time(), dados["texto"]))
            self.ditas = ditas[-30:]
        await self.ws.send_text(json.dumps(dados, ensure_ascii=False))
        if dados.get("tipo") in _ESPELHAR:
            await _espelhar(dados)
        self._gravar_conversa(dados)

    def _gravar_conversa(self, dados: dict) -> None:
        import conversas as conv
        tipo = dados.get("tipo")
        try:
            if tipo == "voce" and not dados.get("parcial"):
                fotos, self.fotos_pendentes = self.fotos_pendentes, []
                conv.registrar(self.conversa, "voce", dados.get("texto", ""),
                               substituir=bool(dados.get("corrigido")), fotos=fotos,
                               arquivos=dados.get("arquivos"))
            elif tipo == "bot":
                conv.registrar(self.conversa, "bot", dados.get("texto", ""), juntar=True)
            elif tipo == "bot_final":
                conv.registrar(self.conversa, "bot", dados.get("texto", ""),
                               substituir=True, rota=dados.get("rota"))
        except Exception as e:  # noqa: BLE001 - disco cheio etc.: a conversa segue
            print("  [conversas] falhou: %s" % str(e)[:80], flush=True)

    # ------------------------------------------------------------------
    def alimentar(self, pcm: np.ndarray) -> np.ndarray | None:
        """Acumula áudio e devolve a utterance quando a fala termina.

        O silêncio é contado em AMOSTRAS, não em `time.time()`: o relógio
        de parede assume que o áudio chega em tempo real, o que é falso
        quando o cliente envia em rajada (teste automatizado, rede
        engasgada, aba em segundo plano). Com relógio de parede a fala
        só "terminava" no envio seguinte — a resposta de uma pergunta
        saía um turno atrasada.
        """
        _o, _v, vad, _l = _modelos()

        # O Silero exige blocos de exatamente 512 amostras.
        dados = np.concatenate([self.resto, pcm]) if len(self.resto) else pcm
        n = (len(dados) // 512) * 512
        self.resto = dados[n:].copy()
        if n == 0:
            return None
        dados = dados[:n]

        p = vad._prob_fala(dados)
        amostras = len(dados)

        if not self.falando:
            if p >= 0.5:
                self.falando = True
                self.t_inicio_fala = time.time()
                self.mudo_amostras = 0
                # Junta o áudio de ANTES do VAD ter certeza: consoantes
                # curtas do início ("P" de "pausa") ficam abaixo do limiar
                # e eram cortadas - "pausa o Spotify" virava "Aosys, Spotify".
                self.buf = self.pre + [dados]
                self.pre = []
            else:
                self.pre.append(dados)
                while sum(len(x) for x in self.pre) > PRE_FALA:
                    self.pre.pop(0)
            return None

        self.buf.append(dados)
        if p >= 0.5:
            self.mudo_amostras = 0
            return None

        self.mudo_amostras += amostras
        if self.mudo_amostras < config.SILENCIO_FIM_S * config.SAMPLE_RATE:
            return None

        audio = np.concatenate(self.buf)
        self.falando = False
        self.buf = []
        self.mudo_amostras = 0
        if len(audio) / config.SAMPLE_RATE < config.FALA_MIN_S:
            return None
        return audio


def _transcrever(audio: np.ndarray, dica: str = "") -> tuple[str, float]:
    ouvido, _v, _vad, _l = _modelos()
    return ouvido.transcrever(audio, dica)


def _sintetizar_edge(texto: str, voz: str, velocidade: float, tom: str = "",
                     filtro: str = ""):
    """Edge TTS → PCM int16 24 kHz, o mesmo formato do Kokoro.

    O Edge devolve MP3; o cliente toca PCM cru. A conversão passa pelo
    ffmpeg **por pipe** — medido: 0,042s contra 0,091s gravando um
    arquivo temporário.

    A velocidade vira `rate=+N%` — o Edge não aceita multiplicador.
    """
    import asyncio as _aio
    import shutil
    import subprocess

    import edge_tts

    nome = voz.split(":", 1)[1] if ":" in voz else voz
    pct = int(round((velocidade - 1.0) * 100))
    rate = "%+d%%" % pct

    async def baixar() -> bytes:
        com = (edge_tts.Communicate(texto, nome, rate=rate, pitch=tom) if tom
               else edge_tts.Communicate(texto, nome, rate=rate))
        dados = b""
        async for p in com.stream():
            if p["type"] == "audio":
                dados += p["data"]
        return dados

    try:
        mp3 = _aio.run(baixar())
    except RuntimeError:
        # já existe laço rodando nesta thread
        laco = _aio.new_event_loop()
        try:
            mp3 = laco.run_until_complete(baixar())
        finally:
            laco.close()

    if not mp3:
        return None

    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg não encontrado (necessário para o Edge TTS)")

    r = subprocess.run(
        [ff, "-v", "quiet", "-i", "pipe:0", *(["-af", filtro] if filtro else []),
         "-f", "s16le", "-ar", str(config.TTS_SR), "-ac", "1", "pipe:1"],
        input=mp3, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("ffmpeg falhou ao converter o áudio do Edge")
    return np.frombuffer(r.stdout, dtype=np.int16)


def _edge_streaming(texto: str, voz: str, velocidade: float):
    """Edge TTS em pedaços: gera PCM conforme o áudio chega.

    Diferença para `_sintetizar_edge`, que espera o MP3 inteiro:

        completo    ~1,20s até o primeiro áudio
        streaming   ~0,91s  (ganho medido: 0,29s)

    O ganho é modesto porque o próprio Edge leva ~0,9s para mandar o
    primeiro pedaço — esse é o piso da rede até a Microsoft.

    O ffmpeg roda como processo contínuo: recebe MP3 pelo stdin e
    devolve PCM pelo stdout sem esperar o arquivo fechar. MP3 não pode
    ser decodificado quadro a quadro isoladamente, mas o decodificador
    mantém o contexto entre as escritas.

    Só é usado por vozes Edge PURAS. As convertidas (`mix:`) precisam do
    áudio completo, porque o RVC converte o arquivo inteiro de uma vez.
    """
    import asyncio as _aio
    import queue
    import shutil
    import subprocess
    import threading

    import edge_tts

    nome = voz.split(":", 1)[1] if ":" in voz else voz
    rate = "%+d%%" % int(round((velocidade - 1.0) * 100))

    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg não encontrado")

    proc = subprocess.Popen(
        [ff, "-v", "quiet", "-i", "pipe:0", "-f", "s16le",
         "-ar", str(config.TTS_SR), "-ac", "1", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    saida: "queue.Queue" = queue.Queue()

    def ler():
        """Lê o PCM decodificado e empurra para a fila."""
        try:
            while True:
                dados = proc.stdout.read(8192)
                if not dados:
                    break
                saida.put(dados)
        finally:
            saida.put(None)

    threading.Thread(target=ler, daemon=True).start()

    def alimentar():
        """Baixa do Edge e escreve no ffmpeg."""
        async def baixar():
            com = edge_tts.Communicate(texto, nome, rate=rate)
            async for p in com.stream():
                if p["type"] == "audio":
                    try:
                        proc.stdin.write(p["data"])
                        proc.stdin.flush()
                    except (BrokenPipeError, OSError):
                        return

        try:
            _aio.run(baixar())
        except RuntimeError:
            laco = _aio.new_event_loop()
            try:
                laco.run_until_complete(baixar())
            finally:
                laco.close()
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    threading.Thread(target=alimentar, daemon=True).start()

    # Entrega os pedaços de PCM conforme saem do decodificador.
    while True:
        item = saida.get()
        if item is None:
            break
        yield np.frombuffer(item, dtype=np.int16)

    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


_gemini_pausa = [0.0]     # até quando não tentar o Gemini (429)


def _sintetizar_gemini(texto: str, voz: str, velocidade: float):
    """Gemini TTS → PCM int16 24 kHz, via streaming.

    Usa `:streamGenerateContent?alt=sse`, que devolve o áudio em pedaços
    conforme são gerados, em vez de esperar o arquivo inteiro. Medido
    nesta máquina, com billing ativo:

        generateContent        1º áudio 3,73s
        streamGenerateContent  1º áudio 2,42s   <- este

    Em conversa só o PRIMEIRO pedaço importa: a partir dele o cliente já
    começa a falar.

    Cota: no tier gratuito os modelos TTS têm limite de **3 requisições
    por minuto** (não os 15 do Flash normal), o que devolve 429 na
    segunda pergunta de qualquer conversa. Com billing ativo o limite
    sobe para 1.000 RPM.
    """
    import base64
    import json as _json
    import urllib.request

    chave = config.chave_gemini()
    if not chave:
        raise RuntimeError("sem GEMINI_API_KEY")

    nome = voz.split(":", 1)[1] if ":" in voz else voz

    # SEM prefixo de estilo. Medido: os modelos TTS do Gemini LEEM a
    # instrução em voz alta em vez de interpretá-la — o áudio saía com
    # "Diga de forma natural e conversacional. Bom dia!". E
    # `systemInstruction`, a forma correta de passar isso, é recusada
    # com HTTP 400 por estes modelos.
    #
    # Consequência: a velocidade não é controlável no Gemini. O
    # parâmetro é aceito na assinatura para manter a interface uniforme
    # com Edge e Kokoro, mas é ignorado aqui.
    corpo = {
        "contents": [{"parts": [{"text": texto}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": nome}}
            },
        },
    }
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "%s:streamGenerateContent?alt=sse" % config.GEMINI_MODELO)
    req = urllib.request.Request(
        url, data=_json.dumps(corpo).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": chave})

    pedacos = []
    with urllib.request.urlopen(req, timeout=90) as r:
        for linha in r:
            linha = linha.decode("utf-8", "ignore").strip()
            if not linha.startswith("data:"):
                continue
            dado = linha[5:].strip()
            if not dado or dado == "[DONE]":
                continue
            try:
                j = _json.loads(dado)
                b64 = (j["candidates"][0]["content"]["parts"][0]
                       ["inlineData"]["data"])
            except (ValueError, KeyError, IndexError):
                continue
            pedacos.append(base64.b64decode(b64))

    if not pedacos:
        raise RuntimeError("Gemini não devolveu áudio")
    return np.frombuffer(b"".join(pedacos), dtype=np.int16)


def _sintetizar_rvc(texto: str, voz: str, velocidade: float):
    """Kokoro gera a fala, o RVC troca o timbre.

    Mantém a latência do Kokoro (~0,17s) e acrescenta ~0,5s — mais
    rápido que qualquer TTS online.

    A conversão roda num serviço separado (src/rvc_servico.py) porque o
    `fairseq` do RVC não funciona no Python 3.11 do servidor web.

    Sobre a taxa de amostragem: os modelos RVC comuns são 40 kHz e o Kokoro
    entrega 24 kHz. Reamostrar a SAÍDA de volta para 24 kHz somava uma
    segunda conversão de taxa e deixava a voz metálica. Agora o áudio
    convertido é enviado ao cliente na taxa do modelo, e o navegador
    toca nela — uma reamostragem a menos.
    """
    import io
    import urllib.parse
    import urllib.request

    _o, kokoro, _vad, _l = _modelos()
    kokoro.carregar()
    nome = voz.split(":", 1)[1] if ":" in voz else voz

    # 1) Kokoro faz a fala base, com a voz padrão local.
    partes = []
    for _g, _p, a in kokoro._pipe(texto, voice=config.TTS_VOZ_RESERVA,
                                  speed=velocidade):
        partes.append(a if isinstance(a, np.ndarray) else a.detach().cpu().numpy())
    if not partes:
        return None
    base = np.concatenate(partes)

    # 2) empacota como WAV e manda ao serviço RVC
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, base, config.TTS_SR, format="WAV", subtype="PCM_16")

    req = urllib.request.Request(
        "%s/converter?voz=%s" % (config.RVC_URL, urllib.parse.quote(nome)),
        data=buf.getvalue(),
        headers={"Content-Type": "audio/wav"})
    with urllib.request.urlopen(req, timeout=30) as r:
        convertido = r.read()

    audio, sr = sf.read(io.BytesIO(convertido), dtype="int16")
    return audio, int(sr)


def _sintetizar_mix(texto: str, voz: str, velocidade: float):
    """Edge TTS gera a base, RVC troca o timbre.

    Formato do id: `mix:<base>:<timbre>` — ex. `mix:Francisca:MinhaVoz`.

    Por que o Edge e não o Kokoro como base: o RVC troca o timbre mas
    herda a cadência — "TTS prosody mistakes propagate; RVC adjusts
    timbre but cannot rewrite cadence" (arquitetura de referência). Com
    base no Kokoro a voz saía mecânica por mais que se ajustasse o RVC.
    Com base no Edge, a entonação já vem de uma voz neural de produção.

    Custo medido: Edge 1,5s + RVC 0,4s ≈ 1,9s — o RVC quase não pesa,
    porque o gargalo é a chamada online.
    """
    import io
    import urllib.parse
    import urllib.request

    partes = voz.split(":")
    if len(partes) != 3:
        raise ValueError("id de voz mix inválido: %s" % voz)
    _, base, alvo = partes

    info = config.BASES_EDGE.get(base)
    if info is None:
        raise ValueError("base desconhecida: %s" % base)

    # 1) Edge gera a fala com prosódia natural.
    #
    # Velocidade fixa em 1.0: o RVC reproduz o ritmo da base, então
    # acelerar aqui sairia acelerado na voz convertida. O controle de
    # velocidade da interface não se aplica a este motor.
    pcm_base = _sintetizar_edge(texto, "edge:%s" % info["voz"], 1.0)
    if pcm_base is None or not len(pcm_base):
        raise RuntimeError("Edge não devolveu áudio para a base")

    # 2) empacota e manda ao serviço RVC para trocar o timbre (liga o
    #    serviço se ainda não estiver no ar: ele é sob demanda)
    if not _rvc_no_ar() and not _rvc_ligar():
        raise RuntimeError("serviço RVC não ligou (veja logs/rvc.err.log)")
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, pcm_base, config.TTS_SR, format="WAV", subtype="PCM_16")

    req = urllib.request.Request(
        "%s/converter?voz=%s" % (config.RVC_URL, urllib.parse.quote(alvo)),
        data=buf.getvalue(),
        headers={"Content-Type": "audio/wav"})
    with urllib.request.urlopen(req, timeout=40) as r:
        convertido = r.read()

    audio, sr = sf.read(io.BytesIO(convertido), dtype="int16")
    return audio, int(sr)


def _sintetizar(frase: str, voz_id: str = None, velocidade: float = None):
    """Frase → (PCM int16, taxa). None quando não há nada para falar.

    Devolve a TAXA junto porque nem todo motor usa 24 kHz: os modelos
    RVC são de 40 ou 48 kHz, e reamostrar para 24 kHz só para uniformizar
    degradava a voz (ficava metálica). O cliente toca na taxa recebida.

    A voz é parâmetro (não `config.TTS_VOZ` direto) para a interface
    poder trocar durante a conversa, sem reiniciar o servidor.

    Vozes com prefixo `rvc:` passam pelo serviço de conversão; `gemini:`
    e `edge:` vão para os motores online; as demais para o Kokoro local.
    Qualquer falha cai para o Kokoro em vez de emudecer.
    """
    _o, voz, _vad, _l = _modelos()
    from fala import limpar

    texto = limpar(frase)
    if not texto:
        return None

    escolhida = voz_id or config.TTS_VOZ
    vel = velocidade or config.TTS_VELOCIDADE

    if escolhida.startswith("mix:"):
        try:
            return _sintetizar_mix(texto, escolhida, vel)
        except Exception as e:  # noqa: BLE001
            import traceback

            print("  [mix] FALHOU em %s: %s" % (escolhida, str(e)[:120]),
                  flush=True)
            traceback.print_exc()
            escolhida = config.TTS_VOZ_RESERVA

    if escolhida.startswith("rvc:"):
        try:
            return _sintetizar_rvc(texto, escolhida, vel)
        except Exception as e:  # noqa: BLE001
            print("  [rvc] FALHOU em %s: %s" % (escolhida, str(e)[:120]),
                  flush=True)
            print("  [rvc] o serviço está no ar? "
                  ".venv-rvc\\Scripts\\python.exe src\\rvc_servico.py",
                  flush=True)
            escolhida = config.TTS_VOZ_RESERVA

    if escolhida.startswith("gemini:") and time.time() < _gemini_pausa[0]:
        # Cota estourada há pouco: nem tenta (cada tentativa custava ~1-3 s
        # por frase antes de cair na reserva).
        escolhida = config.TTS_VOZ_RESERVA

    if escolhida.startswith("gemini:"):
        try:
            return _sintetizar_gemini(texto, escolhida, vel), config.TTS_SR
        except Exception as e:  # noqa: BLE001
            import traceback

            print("  [gemini] FALHOU em %s: %s" % (escolhida, e), flush=True)
            if "429" in str(e):
                _gemini_pausa[0] = time.time() + 600
                print("  [gemini] cota esgotada: reserva direto por 10 min", flush=True)
            else:
                traceback.print_exc()
            print("  [gemini] usando Kokoro (%s) como reserva"
                  % config.TTS_VOZ_RESERVA, flush=True)
            escolhida = config.TTS_VOZ_RESERVA

    if escolhida.startswith("jarvis:"):
        try:
            v = vel * (1 + config.JARVIS_RITMO / 100)
            return _sintetizar_edge(texto, escolhida, v, config.JARVIS_TOM,
                                    config.JARVIS_FILTRO), config.TTS_SR
        except Exception as e:  # noqa: BLE001
            print("  [jarvis] FALHOU em %s: %s" % (escolhida, str(e)[:120]), flush=True)
            escolhida = config.TTS_VOZ_RESERVA

    if escolhida.startswith("edge:"):
        try:
            return _sintetizar_edge(texto, escolhida, vel), config.TTS_SR
        except Exception as e:  # noqa: BLE001
            import traceback

            print("  [edge] FALHOU em %s: %s" % (escolhida, e), flush=True)
            traceback.print_exc()
            print("  [edge] usando Kokoro (%s) como reserva"
                  % config.TTS_VOZ_RESERVA, flush=True)
            escolhida = config.TTS_VOZ_RESERVA

    partes = []
    voz.carregar()
    for _g, _p, a in voz._pipe(texto, voice=escolhida, speed=vel):
        partes.append(a if isinstance(a, np.ndarray) else a.detach().cpu().numpy())
    if not partes:
        return None
    audio = np.concatenate(partes)
    pcm = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    return pcm, config.TTS_SR


def _hermes_stream(mensagens: list, ao_ferramenta, cancelar: threading.Event,
                   modelo: str = ""):
    """Streaming do API server do Hermes, lido direto em SSE.

    O cliente `openai` descarta eventos com nome próprio; o Hermes manda
    o progresso das ferramentas justamente assim:

        event: hermes.tool.progress
        data: {"tool": "terminal", "label": "date", "status": "running", ...}

    Então o SSE é lido à mão: texto vira `yield`, ferramenta vira
    `ao_ferramenta(dict)`. As linhas `: keepalive` (a cada 10s durante
    ferramentas longas) são ignoradas.
    """
    import httpx

    corpo = {
        "model": config.HERMES_MODELO,
        "stream": True,
        "messages": ([{"role": "system", "content": config.SISTEMA_HERMES}]
                     + [m for m in mensagens if m["role"] != "system"]),
    }
    # Modelo escolhido na tela (Opus, Fable, Sonnet, Haiku...). O API server
    # só honra um `model` diferente do virtual quando vem `provider` junto,
    # então os dois andam sempre em par; vazio = padrão do perfil do Hermes.
    if modelo and config.modelo_claude_valido(modelo):
        corpo["model"] = modelo
        corpo["provider"] = config.provedor_modelo(modelo)
    cab = {"Authorization": "Bearer %s" % config.chave_hermes(),
           "Content-Type": "application/json"}
    evento = None
    with httpx.stream("POST", config.HERMES_URL + "/chat/completions",
                      json=corpo, headers=cab,
                      timeout=httpx.Timeout(300, connect=8)) as r:
        if r.status_code != 200:
            r.read()
            raise RuntimeError("Hermes HTTP %s: %s"
                               % (r.status_code, r.text[:150]))
        for linha in r.iter_lines():
            if cancelar.is_set():
                # Fechar a conexão avisa o Hermes; ele interrompe o turno.
                return
            if not linha or linha.startswith(":"):
                if not linha:
                    evento = None
                continue
            if linha.startswith("event:"):
                evento = linha[6:].strip()
                continue
            if not linha.startswith("data:"):
                continue
            dado = linha[5:].strip()
            if dado == "[DONE]":
                return
            try:
                j = json.loads(dado)
            except ValueError:
                continue
            if evento == "hermes.tool.progress":
                ao_ferramenta(j)
                continue
            for c in j.get("choices") or []:
                t = (c.get("delta") or {}).get("content")
                if t:
                    yield t


# Aviso falado quando o Hermes começa a usar ferramentas: silêncio de
# vários segundos parece travamento; um "deixa eu ver" parece trabalho.

_hermes_cache = [0.0, False]


def _hermes_online() -> bool:
    """Hermes respondendo? Cache de 20s para não custar em cada frase."""
    import urllib.request

    agora = time.time()
    if agora - _hermes_cache[0] < 20:
        return _hermes_cache[1]
    try:
        req = urllib.request.Request(
            config.HERMES_URL + "/models",
            headers={"Authorization": "Bearer %s" % config.chave_hermes()})
        urllib.request.urlopen(req, timeout=4).read()
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    _hermes_cache[:] = [agora, ok]
    return ok


async def _falar_uma(s: Sessao, texto: str) -> None:
    """Sintetiza e envia uma frase avulsa (comando pronto, fim de timer)."""
    res = await asyncio.to_thread(_sintetizar, texto, s.voz, s.velocidade)
    await s.enviar(tipo="estado", estado="falando")
    await s.enviar(tipo="bot", texto=texto)
    if res is not None:
        pcm, taxa = res
        await s.enviar(tipo="taxa", hz=int(taxa))
        await s.ws.send_bytes(pcm.tobytes())


_versao_mod: dict[str, float] = {}


def _sem_commit(arquivo: str) -> bool:
    """O arquivo difere do último commit? (falha do git = deixa passar)"""
    import subprocess
    try:
        r = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", arquivo],
                           cwd=str(config.RAIZ), capture_output=True, timeout=5)
        return r.returncode == 1
    except Exception:  # noqa: BLE001
        return False


def _atualizado(nome: str):
    """Importa o módulo e o recarrega se o arquivo mudou no disco.

    Assim um comando novo em comandos.py (ou ajuste no juiz/ativação) vale
    na frase seguinte, SEM reiniciar o servidor. Reiniciar pelo Hermes
    derrubava a própria conversa que pediu o reinício. Se o arquivo novo
    tiver erro, mantém a versão antiga que funcionava.
    """
    import importlib

    mod = importlib.import_module(nome)
    try:
        mt = os.path.getmtime(mod.__file__)
    except OSError:
        return mod
    antes = _versao_mod.setdefault(nome, mt)
    if mt != antes:
        _versao_mod[nome] = mt
        # TRAVA: mudança sem commit (editada direto, fora de propostas.py)
        # não entra. Só vale o que passou nos testes e foi aprovado.
        if _sem_commit(mod.__file__):
            print("  [trava] %s.py mudou sem aprovação: ignorado. Use "
                  "src/propostas.py (testes + aprovação do usuário)." % nome, flush=True)
            return mod
        try:
            mod = importlib.reload(mod)
            if hasattr(mod, "_APPS_PRONTO"):
                mod._APPS_PRONTO.wait(20)   # comandos: relê o menu Iniciar
            print("  [recarregado] %s.py" % nome, flush=True)
        except Exception as e:  # noqa: BLE001
            print("  [recarregado] %s.py com ERRO, mantendo o anterior: %s"
                  % (nome, str(e)[:150]), flush=True)
            mod = sys.modules[nome]
    return mod


async def _comando_pronto(s: Sessao, texto: str, t_fim_fala: float,
                          t_stt: float) -> bool:
    """Tenta resolver `texto` como comando pronto. True se resolveu."""
    comandos = await asyncio.to_thread(_atualizado, "comandos")

    t0 = time.time()
    try:
        r = await asyncio.to_thread(comandos.tentar, texto)
    except Exception as e:  # noqa: BLE001
        print("  [comando] erro: %s" % str(e)[:120], flush=True)
        return False
    if r is None:
        return False
    t_acao = time.time() - t0
    print("  [comando] %s (%.2fs): %s" % (r.nome, t_acao, texto[:60]), flush=True)
    await _entregar(s, r, texto, t0, t_acao, t_stt, t_fim_fala, "comando")
    return True


# Portão de áudio (detector de "Luna", voz real: nome ~0,95+, fundo ~0).
PORTAO_NOME = 0.2     # abaixo disso nem transcreve
PORTAO_TEXTO = 0.5    # "Luna" no meio da frase precisa de pelo menos isto


def _acordada(s: Sessao) -> bool:
    """Dentro da janela em que vale falar sem dizer o nome de novo."""
    fim = s.acordado_ate if s.continua else s.nome_ate
    # Conta quando COMEÇOU a falar: frase longa iniciada na janela vale.
    return time.time() < fim or (0 < s.t_inicio_fala < fim)


async def _acao_local(s: Sessao, texto: str, d: dict, t_fim_fala: float,
                      t_stt: float) -> bool:
    """Executa a ação que o roteador escolheu (src/acoes.py). True se fez."""
    acoes = await asyncio.to_thread(_atualizado, "acoes")
    t0 = time.time()
    nome, args = d["acao"], d["args"]
    r = await asyncio.to_thread(acoes.executar, nome, args)
    if r is None:
        print("  [acao] %s %s inválida, segue: %s" % (nome, args, texto[:50]), flush=True)
        return False
    t_acao = d["t"] + time.time() - t0
    print("  [acao] %s %s (%.2fs): %s" % (nome, json.dumps(args, ensure_ascii=False)[:60],
                                          t_acao, texto[:60]), flush=True)
    # Para candidatos.py: padrões que valeria escrever (frases frequentes).
    try:
        with open(os.path.join(config.RAIZ, "logs", "acoes.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "texto": texto, "acao": nome, "args": args},
                               ensure_ascii=False) + chr(10))
    except OSError:
        pass
    await _entregar(s, r, texto, t0, t_acao, t_stt, t_fim_fala, "acao")
    return True


def _ajuda_texto() -> tuple[str, str]:
    """(fala curta, lista completa em markdown) do que a Luna faz DE VERDADE.

    Vem do CATALOGO de comandos.py (conferido pelo teste_comandos), dos
    atalhos aprovados em atalhos.json e do que está conectado agora.
    """
    comandos = _atualizado("comandos")
    linhas = ["**O que eu faço na hora, sem IA:**", ""]
    for titulo, _nome, exemplos in comandos.CATALOGO:
        linhas.append("- **%s**: %s" % (titulo, " · ".join("“%s”" % e for e in exemplos)))
    try:
        import aprendizado
        meus = [a for a in aprendizado.atalhos() if a["ligado"]]
    except Exception:  # noqa: BLE001
        meus = []
    if meus:
        linhas += ["", "**Atalhos que aprendi com você:**", ""]
        def _ex(a):
            e = (a.get("exemplo") or "").strip().strip('“”"')
            return ": “%s”" % e if e else ""
        linhas += ["- **%s**%s" % (a["nome"], _ex(a)) for a in meus[:12]]
    claude = _hermes_online()
    linhas += ["", "**E conversando:**", "",
               "- Perguntas, explicações e textos com a IA principal (%s)." % config.LLM_NOME,
               "- Cole um print ou anexe um PDF e pergunte sobre ele."]
    if claude:
        linhas.append("- Tarefas no computador (arquivos, pesquisa, organizar coisas) com o **Claude**, "
                      "que também lembra de você e aprende habilidades novas.")
    else:
        linhas.append("- Com o **Claude** conectado (Hermes), eu também faço tarefas no computador e aprendo "
                      "com o uso. Veja Ajustes → Inteligência.")
    linhas += ["", "Sempre com “Luna” na frase. Para parar: “Luna, para”."]
    fala = ("Na hora, sem IA, eu vejo hora e clima, mexo no volume e na música, abro programas e sites, "
            "faço contas e timers. O resto eu converso com a IA%s. Deixei a lista completa na tela."
            % (", e as tarefas no computador eu passo para o Claude" if claude else ""))
    return fala, "\n".join(linhas)


async def _entregar(s: Sessao, r, texto: str, t0: float, t_acao: float,
                    t_stt: float, t_fim_fala: float, origem: str) -> None:
    """Fala o resultado de um comando/ação, arma timers e registra."""
    lista_ajuda = ""
    if r.especial == "ajuda":
        r.fala, lista_ajuda = await asyncio.to_thread(_ajuda_texto)
    if r.especial == "cancelar_timers":
        n = sum(1 for t in s.timers if not t.done())
        for t in s.timers:
            t.cancel()
        s.timers.clear()
        r.fala = ("Nenhum timer ligado." if not n else
                  "Timer cancelado." if n == 1 else f"{n} timers cancelados.")

    if r.depois:
        seg, fala_fim = r.depois

        async def _disparar():
            await asyncio.sleep(seg)
            try:
                await s.enviar(tipo="aviso", texto="⏰ " + fala_fim)
                await _falar_uma(s, fala_fim)
                await s.enviar(tipo="estado", estado="ouvindo")
            except Exception:  # noqa: BLE001 - aba fechada
                pass

        s.timers = [t for t in s.timers if not t.done()]
        s.timers.append(asyncio.create_task(_disparar()))

    # Aparece no painel Atividade como qualquer ferramenta do Hermes.
    ident = "cmd-%d" % int(t0 * 1000)
    await s.enviar(tipo="ferramenta", ferramenta=origem + " · " + r.nome,
                   rotulo=texto[:80], id=ident, status="running")
    await s.enviar(tipo="ferramenta", ferramenta=origem + " · " + r.nome,
                   rotulo="", id=ident, status="completed")

    await _falar_uma(s, r.fala)
    if lista_ajuda:
        # Fala o resumo; na tela fica a lista inteira (com markdown).
        await s.enviar(tipo="bot_final", texto=lista_ajuda, rota="comando")
    s.historico.append({"role": "assistant", "content": r.fala})
    if len(s.historico) > 9:
        s.historico[:] = s.historico[:1] + s.historico[-8:]
    await s.enviar(tipo="metrica", stt=round(t_stt, 2), llm=round(t_acao, 2),
                   total=round(time.time() - t_fim_fala, 2), cerebro="comando",
                   origem=origem, ferramentas=0, cancelado=False)
    await s.enviar(tipo="estado", estado="ouvindo")
    s.ocupado = False


async def _olhar_tela(s: Sessao, texto: str, t_fim_fala: float, t_stt: float,
                      llm, via_claude: bool, imagens: list | None = None) -> None:
    """ "Luna, olha minha tela": print do monitor do mouse + modelo de visão.

    Local (Qwen3-VL, ~1 s) por padrão; Claude quando o cérebro está no
    Claude ou você pediu "pensa melhor" sobre a última tela. A imagem fica
    só na memória da sessão (para o "pensa melhor"), nunca em disco.
    """
    tela = _atualizado("tela")
    t0 = time.time()
    ident = "tela-%d" % int(t0 * 1000)
    await s.enviar(tipo="estado", estado="pensando")
    rotulo_ferr = "imagem · olhar" if imagens else "tela · olhar"
    await s.enviar(tipo="ferramenta", ferramenta=rotulo_ferr,
                   rotulo=("Claude" if via_claude else "rápido") + ": " + texto[:60],
                   id=ident, status="running")
    try:
        if imagens:
            urls, pergunta, instr = list(imagens), texto, tela.INSTRUCAO_IMAGEM
        elif s.ultima_tela and via_claude and s.ultima_tela[1] > time.time() - 120 \
                and _atualizado("juiz").pede_escalar(texto):
            urls, pergunta, instr = s.ultima_tela[0], s.ultima_tela[2], s.ultima_tela[3]
        else:
            url, _tam = await asyncio.to_thread(tela.capturar)
            urls, pergunta, instr = [url], texto, tela.INSTRUCAO
        s.ultima_tela = (urls, time.time(), pergunta, instr)
        if via_claude and await asyncio.to_thread(_hermes_online):
            await s.enviar(tipo="aviso", texto="Olhando com calma…", som="pensar")
            msgs = [{"role": "user", "content": [
                *({"type": "image_url", "image_url": {"url": u}} for u in urls),
                {"type": "text", "text": instr + "\n\nPergunta: " + pergunta}]}]
            s.cancelar.clear()

            def _junta():
                return "".join(_hermes_stream(msgs, lambda _e: None, s.cancelar,
                                              s.modelo_claude))
            resposta = (await asyncio.to_thread(_junta)).strip()
            origem = "hermes"
        else:
            resposta, _dt = await asyncio.to_thread(
                tela.olhar_local, *_cerebro_rapido()[:2], pergunta, urls,
                {"extra_body": _cerebro_rapido()[2]}, 260, instr)
            origem = "rapido"
    except Exception as e:  # noqa: BLE001
        print("  [tela] falhou: %s" % str(e)[:120], flush=True)
        resposta, origem = "Não consegui olhar a tela agora.", "erro"
    dt = time.time() - t0
    print("  [tela] %s %.1fs: %s -> %s" % (origem, dt, texto[:40], resposta[:80]), flush=True)
    await s.enviar(tipo="ferramenta", ferramenta=rotulo_ferr, rotulo="",
                   id=ident, status="completed")
    resposta = resposta or "Não consegui entender o que aparece na tela."
    await _falar_uma(s, resposta)
    s.historico.append({"role": "assistant", "content": resposta})
    if len(s.historico) > 9:
        s.historico[:] = s.historico[:1] + s.historico[-8:]
    await s.enviar(tipo="metrica", stt=round(t_stt, 2), llm=round(dt, 2),
                   total=round(time.time() - t_fim_fala, 2), cerebro=origem,
                   origem="imagem" if imagens else "tela", ferramentas=1,
                   cancelado=False)
    await s.enviar(tipo="estado", estado="ouvindo")


def _guardar_amostra(audio: np.ndarray, texto: str, chamou: bool) -> None:
    """Guarda falas curtas (até 4 s) em dados_luna/voz_real/, com o texto.

    São a matéria-prima do "Treinar com a minha voz" (src/treino_voz.py).
    Fica no PC, fora do git. Guarda as MAIS NOVAS: até 300 de cada tipo
    (com e sem o nome); passando disso, a mais antiga daquele tipo sai.
    """
    try:
        if len(audio) > 4 * config.SAMPLE_RATE:
            return
        pasta = config.RAIZ / "dados_luna" / "voz_real"
        pasta.mkdir(parents=True, exist_ok=True)
        tipo = "_nome" if chamou else "_sem"
        velhas = sorted(pasta.glob("*%s.wav" % tipo))
        for w in velhas[:max(0, len(velhas) - 299)]:
            w.unlink(missing_ok=True)
            w.with_suffix(".txt").unlink(missing_ok=True)
        import soundfile as sf
        # Ano no nome: a ordem alfabética é a ordem do tempo.
        nome = time.strftime("%Y%m%d-%H%M%S") + tipo
        sf.write(str(pasta / (nome + ".wav")), audio, config.SAMPLE_RATE)
        (pasta / (nome + ".txt")).write_text(texto, encoding="utf-8")
    except Exception:  # noqa: BLE001 - amostra é opcional
        pass


async def _responder(s: Sessao, audio: np.ndarray | None = None,
                     texto_digitado: str | None = None, falado: bool = False,
                     imagens: list | None = None, forcar: str | None = None,
                     contexto: str | None = None, nomes: list | None = None) -> None:
    """Fluxo completo de um turno, com o áudio saindo frase a frase.

    Entrada por voz (`audio`) ou digitada (`texto_digitado`, pula o STT).
    """
    t_fim_fala = time.time()
    cerebro = forcar or s.cerebro      # "hermes" = Expert (tudo ao Claude)
    _o, _v, _vad, llm = _modelos()
    from fala import frases

    if texto_digitado is not None:
        texto, t_stt = texto_digitado.strip(), 0.0
    else:
        # Dica ao Whisper: a última fala do assistente (grafia garantida
        # de nomes e do assunto). Nunca a fala do usuário - um erro dela
        # passaria a se repetir nas próximas transcrições.
        dica = next((m["content"] for m in reversed(s.historico)
                     if m["role"] == "assistant"), "")
        nota_nome = None
        if s.ativacao and not _acordada(s):
            # PORTÃO: o detector (treinado com a sua voz) ouve o começo da
            # fala; sem nome, nem transcreve. Som da caixa/TV morre aqui.
            try:
                det = _atualizado("detector_luna")
                if det.disponivel():
                    nota_nome = await asyncio.to_thread(
                        det.pontuar, audio[:int(2.5 * config.SAMPLE_RATE)])
            except Exception as e:  # noqa: BLE001 - sem detector: segue pelo texto
                print("  [detector] falhou: %s" % str(e)[:80], flush=True)
            if nota_nome is not None and nota_nome < PORTAO_NOME:
                print("  [portao] %.2f sem nome, nem transcreveu" % nota_nome, flush=True)
                await s.enviar(tipo="ignorado", texto="")
                await s.enviar(tipo="estado", estado="ouvindo")
                s.ocupado = False
                return
        if s.ativacao:
            # Com a chamada por nome ligada, o nome vai junto na dica: o
            # Whisper escreve "Luna." em vez de "Lona"/"Lê" (medido).
            dica = (_atualizado("ativacao").DICA + " " + dica[-120:]).strip()
        texto, t_stt = await asyncio.to_thread(_transcrever, audio, dica)
    if not texto:
        await s.enviar(tipo="estado", estado="ouvindo")
        s.ocupado = False
        return

    # Palavra de ativação: fala sem "Hermes" fora da janela de conversa é
    # papo do ambiente (TV, outra pessoa) - descarta sem responder.
    if s.ativacao and texto_digitado is None:
        ativacao = _atualizado("ativacao")

        chamou, pedido = ativacao.detectar(texto)
        if nota_nome is not None:
            det = _atualizado("detector_luna")
            certeza = nota_nome >= det.RESGATE
            if chamou and not (certeza or ativacao.nome_no_comeco(texto)
                               or nota_nome >= PORTAO_TEXTO):
                # A dica ao Whisper inventou o nome ("Lona preta" -> "Luna").
                print("  [detector] vetou %.2f: %s" % (nota_nome, texto[:60]), flush=True)
                chamou, pedido = False, ""
            elif not chamou and nota_nome >= 0.99 and len(texto.split()) <= 4 \
                    and len(audio) < 3.0 * config.SAMPLE_RATE and (
                    len(texto.split()) <= 3 or ativacao.parece_pedido(texto)) \
                    and not ativacao.eco_da_luna(getattr(s, "ditas", []), texto):
                # O Whisper sumiu com o nome ("Luna, play" -> "Play."). Só
                # vale para fala CURTA (até 4 palavras, < 3 s): frase longa
                # sem "Luna" no texto é conversa na sala, mesmo com o detector
                # em 1.00 ("E bota aí a custom, bota dois dólares aí..." e
                # "O que foi loucura foi a quantidade de tempo..." passaram).
                curto = len(audio) < 1.6 * config.SAMPLE_RATE and len(texto.split()) <= 3
                print("  [detector] nome pelo áudio %.2f: %s" % (nota_nome, texto[:60]),
                      flush=True)
                chamou, pedido = True, ("" if curto else ativacao.tirar_parecido(texto))
        if audio is not None:
            _guardar_amostra(audio, texto, chamou)
        acordado = _acordada(s)
        if not chamou and acordado and s.continua:
            ativ = _atualizado("ativacao")
            if not ativ.vale_na_janela(texto, getattr(s, "ditas", [])):
                print("  [ativacao] janela, nao parece pedido: %s" % texto[:80], flush=True)
                acordado = False
        if not chamou and not acordado:
            print("  [ativacao] ignorado: %s" % texto[:80], flush=True)
            await s.enviar(tipo="ignorado", texto=texto)
            await s.enviar(tipo="estado", estado="ouvindo")
            s.ocupado = False
            return
        if chamou:
            print("  [ativacao] chamou: %s" % texto[:80], flush=True)
            await s.enviar(tipo="estado", estado="pensando")
        # Janela aberta enquanto responde e toca; quando o áudio acaba o
        # navegador manda "fim_audio" e ela vira JANELA_S para emendar.
        s.acordado_ate = time.time() + 60
        await s.enviar(tipo="acordado")
        if chamou and not pedido:
            # Só o nome: abre a escuta EM SILÊNCIO. Responder "Oi?" aqui
            # atropelava quem já emendava o pedido logo depois do nome.
            print("  [ativacao] acordou (sem responder)", flush=True)
            s.so_nome = True
            await s.enviar(tipo="voce", texto=texto, digitado=False)
            s.acordado_ate = time.time() + ativacao.JANELA_NOME_S
            s.nome_ate = s.acordado_ate
            await s.enviar(tipo="acordado", ate=ativacao.JANELA_NOME_S)
            await s.enviar(tipo="estado", estado="ouvindo")
            s.ocupado = False
            return
        s.nome_ate = 0.0
        if chamou:
            texto = pedido
        if ativacao.encerra(texto):
            # "Obrigado", "valeu", "só isso": fecha a conversa na hora.
            print("  [ativacao] encerrou: %s" % texto[:60], flush=True)
            s.acordado_ate = 0.0
            await s.enviar(tipo="acordado", ate=0)
            await s.enviar(tipo="voce", texto=texto, digitado=False)
            await _falar_uma(s, "Tá bom.")
            await s.enviar(tipo="estado", estado="ouvindo")
            s.ocupado = False
            return

    if not falado:        # da fila falada: a tela já mostrou ao enfileirar
        await s.enviar(tipo="voce", texto=texto, digitado=texto_digitado is not None,
                       arquivos=nomes)

    if texto.strip().lower().rstrip(".!?") in {
        "para", "pare", "chega", "tchau", "encerrar"
    }:
        await s.enviar(tipo="estado", estado="encerrado")
        s.ocupado = False
        return

    s.historico.append({"role": "user", "content": texto})
    if contexto:
        # Arquivo anexado: o modelo recebe o texto do arquivo; o histórico
        # guarda só o pedido + nome (o documento não pesa nos próximos turnos).
        s.historico[-1]["content"] = contexto
        s.contexto_trocar = "[anexo: %s] %s" % (", ".join(nomes or []), texto)

    # "Olha minha tela": antes dos comandos, para "vê isso aqui" não virar
    # outra ação. "pensa melhor" logo depois de olhar reanalisa a MESMA
    # imagem com o Claude.
    tela_mod = _atualizado("tela")
    juiz_mod = _atualizado("juiz")
    if imagens:
        # Imagem pelo chat: Auto -> cérebro rápido (vê imagem, ~2 s);
        # Expert ou "pensa melhor" depois -> Claude com a MESMA imagem.
        s.historico[-1]["content"] = "[imagem] " + texto
        await _olhar_tela(s, texto, t_fim_fala, t_stt, llm, cerebro == "hermes",
                          imagens=imagens)
        return
    recente = s.ultima_tela is not None and s.ultima_tela[1] > time.time() - 120
    if tela_mod.pede_ver(texto) or (recente and juiz_mod.pede_escalar(texto)):
        via_claude = cerebro == "hermes" or juiz_mod.pede_escalar(texto)
        await _olhar_tela(s, texto, t_fim_fala, t_stt, llm, via_claude)
        return

    # Comandos prontos (hora, volume, timer, abrir app...): resolvidos
    # na hora, sem LLM nem Hermes. Não casou -> segue o fluxo normal.
    # Expert (cérebro "hermes"): tudo direto ao Claude, sem comandos
    # prontos, corretor nem roteador. Auto é o modo do dia a dia.
    expert = cerebro == "hermes"
    if not expert and await _comando_pronto(s, texto, t_fim_fala, t_stt):
        return

    # Não casou com comando pronto: o Qwen conserta erros de reconhecimento
    # ("abre o espoti fai") e tenta o comando pronto de novo. Só na voz -
    # texto digitado já vem certo.
    if (texto_digitado is None or falado) and not expert:
        corretor = _atualizado("corretor")
        novo, t_corr = await asyncio.to_thread(
            corretor.corrigir, *_cerebro_rapido()[:2], texto, _cerebro_rapido()[2])
        if corretor.mudou(texto, novo):
            print("  [corretor] %.2fs: %r -> %r" % (t_corr, texto[:60], novo[:60]),
                  flush=True)
            texto = novo
            s.historico[-1]["content"] = texto
            await s.enviar(tipo="voce", texto=texto, digitado=False, corrigido=True)
            if await _comando_pronto(s, texto, t_fim_fala, t_stt + t_corr):
                return

    # Nenhum padrão casou: o ROTEADOR decide numa chamada só (src/roteador.py):
    # ação da lista (executa aqui), conversa (modelo rápido) ou Claude.
    # Antes eram 3 camadas + 4 regras de texto que se atropelavam.
    rota, t_juiz = cerebro, 0.0
    if cerebro == "auto":
        juiz = _atualizado("juiz")
        anterior = [m for m in s.historico[:-1] if m["role"] == "user"]
        if juiz.pede_escalar(texto) and anterior:
            # "pensa melhor": a MESMA pergunta anterior vai ao Claude.
            s.historico[-1]["content"] = (
                "A resposta rápida anterior não bastou. Pense com cuidado e "
                "responda de novo à minha pergunta: " + anterior[-1]["content"])
            rota = "hermes"
        else:
            roteador = _atualizado("roteador")
            ult = getattr(s, "ultima_rota", ("", 0.0))
            ctx = (roteador.contexto_de(s.historico, ult[0])
                   if time.time() - ult[1] < 180 else "")
            llm, modelo, extra = _cerebro_rapido()
            d = await asyncio.to_thread(roteador.decidir, llm, modelo, texto, ctx, extra)
            t_juiz = d["t"]
            if d["destino"] == "acao":
                if await _acao_local(s, texto, d, t_fim_fala, t_stt):
                    s.ultima_rota = ("acao", time.time())
                    return
                rota = "rapido"
            else:
                rota = "hermes" if d["destino"] == "claude" else "rapido"
        if rota == "hermes" and not await asyncio.to_thread(_hermes_online):
            print("  [juiz] Hermes fora do ar, fica no local", flush=True)
            rota = "rapido"
        print("  [juiz] %s (%.2fs): %s" % (rota, t_juiz, texto[:60]), flush=True)
    elif cerebro == "rapido":
        # Modo "só local": ações continuam valendo, nunca vai ao Claude.
        roteador = _atualizado("roteador")
        llm, modelo, extra = _cerebro_rapido()
        d = await asyncio.to_thread(roteador.decidir, llm, modelo, texto, "", extra)
        if d["destino"] == "acao" and await _acao_local(s, texto, d, t_fim_fala, t_stt):
            return
    s.ultima_rota = (rota, time.time())
    await s.enviar(tipo="rota", rota=rota)

    await s.enviar(tipo="estado", estado="pensando")

    fila: asyncio.Queue = asyncio.Queue()
    laco = asyncio.get_running_loop()
    t_primeiro_token: list[float | None] = [None]
    cancelar = s.cancelar
    cancelar.clear()
    avisou = [False]
    bruto: list[str] = []      # resposta com markdown, para a tela formatar
    # Digitado na tela: a resposta é LIDA, então pode ter formatação; a voz
    # fala só o começo (resumo) e o resto fica na conversa.
    digitado = texto_digitado is not None and not falado
    msgs_llm = list(s.historico)
    if digitado and msgs_llm and msgs_llm[-1].get("role") == "user":
        msgs_llm[-1] = dict(msgs_llm[-1], content=str(msgs_llm[-1]["content"]) + config.NOTA_DIGITADO)
    falados = [0]

    if rota == "hermes" and cerebro == "auto":
        # Troca automática para o Claude: avisa JÁ, antes dos ~10s dele,
        # para o silêncio não parecer travamento.
        # Som suave no navegador em vez de falar "Deixa eu pensar nisso".
        avisou[0] = True
        await s.enviar(tipo="aviso", texto="Pensando…", som="pensar")

    def ao_ferramenta(ev: dict) -> None:
        """Roda na thread do LLM: repassa o progresso para a tela."""
        laco.call_soon_threadsafe(fila.put_nowait, ("__ferramenta__", ev))
        if ev.get("status") == "running" and not avisou[0]:
            avisou[0] = True
            laco.call_soon_threadsafe(fila.put_nowait, ("__aviso__", ("Trabalhando nisso…", None)))

    def gerar():
        """Roda em thread: consome o LLM e empurra frases prontas."""
        t0 = time.time()

        def deltas():
            if rota == "hermes":
                fonte = _hermes_stream(msgs_llm, ao_ferramenta, cancelar,
                                       s.modelo_claude)
            else:
                cli, modelo_r, extra_r = _cerebro_rapido()
                fluxo = cli.chat.completions.create(
                    model=modelo_r,
                    messages=msgs_llm,
                    stream=True,
                    temperature=0.6,
                    max_tokens=1200 if digitado else 180,
                    extra_body=extra_r,
                )

                def _local():
                    for ev in fluxo:
                        if cancelar.is_set():
                            try:
                                fluxo.close()
                            except Exception:  # noqa: BLE001
                                pass
                            return
                        escolhas = getattr(ev, "choices", None)
                        if not escolhas:
                            continue
                        delta = getattr(escolhas[0], "delta", None)
                        d = (getattr(delta, "content", None) or "") if delta else ""
                        if d:
                            yield d
                fonte = _local()
            for d in fonte:
                if t_primeiro_token[0] is None:
                    t_primeiro_token[0] = time.time() - t0
                bruto.append(d)
                yield d

        try:
            for frase in frases(deltas()):
                if cancelar.is_set():
                    break
                if digitado and falados[0] > config.FALA_MAX_DIGITADO:
                    # Já falou o resumo: o resto só aparece na tela.
                    laco.call_soon_threadsafe(fila.put_nowait, (frase, None))
                    continue
                falados[0] += len(frase)
                # Vozes Edge puras entregam o áudio em pedaços, conforme
                # a Microsoft envia — o usuário começa a ouvir ~0,3s
                # antes. As demais (Kokoro, Gemini, mix) sintetizam de
                # uma vez: o RVC precisa do arquivo inteiro, e o Kokoro
                # é rápido o bastante para não compensar a complexidade.
                if (s.voz or "").startswith("edge:"):
                    primeiro = True
                    for pedaco in _edge_streaming(frase, s.voz, s.velocidade):
                        if cancelar.is_set():
                            break
                        if not len(pedaco):
                            continue
                        laco.call_soon_threadsafe(
                            fila.put_nowait,
                            (frase if primeiro else None,
                             (pedaco, config.TTS_SR)))
                        primeiro = False
                else:
                    res = _sintetizar(frase, s.voz, s.velocidade)
                    if cancelar.is_set():
                        break
                    laco.call_soon_threadsafe(fila.put_nowait, (frase, res))
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            laco.call_soon_threadsafe(fila.put_nowait, ("__erro__", str(e)))
        finally:
            laco.call_soon_threadsafe(fila.put_nowait, None)

    asyncio.create_task(asyncio.to_thread(gerar))

    partes, primeiro_audio = [], None
    ferramentas = 0
    while True:
        item = await fila.get()
        if item is None:
            break
        frase, res = item
        if frase == "__erro__":
            await s.enviar(tipo="erro", texto=str(res)[:200])
            break
        if frase == "__ferramenta__":
            if res.get("status") == "running":
                ferramentas += 1
            await s.enviar(tipo="ferramenta", ferramenta=res.get("tool"),
                           rotulo=res.get("label") or "",
                           id=res.get("toolCallId") or "",
                           status=res.get("status") or "")
            continue
        if frase == "__aviso__":
            aviso, r_aviso = res
            await s.enviar(tipo="aviso", texto=aviso, som="pensar" if r_aviso is None else "")
            if r_aviso is not None:
                pcm, taxa = r_aviso
                await s.enviar(tipo="taxa", hz=int(taxa))
                await s.ws.send_bytes(pcm.tobytes())
            continue
        # `frase=None` = pedaço seguinte da mesma frase (streaming do
        # Edge): manda o áudio sem repetir o texto na tela.
        if frase is not None:
            partes.append(frase)
        if primeiro_audio is None:
            primeiro_audio = time.time() - t_fim_fala
            await s.enviar(tipo="estado", estado="falando")
        if frase is not None:
            await s.enviar(tipo="bot", texto=frase)
        if res is not None:
            pcm, taxa = res
            # A taxa vai ANTES do áudio: modelos RVC são 40/48 kHz e o
            # cliente precisa saber em que taxa tocar, senão a voz sai
            # acelerada ou arrastada.
            await s.enviar(tipo="taxa", hz=int(taxa))
            await s.ws.send_bytes(pcm.tobytes())

    troca = getattr(s, "contexto_trocar", None)
    if troca:
        for m in reversed(s.historico):
            if m.get("role") == "user":
                m["content"] = troca
                break
        s.contexto_trocar = None
    completa = " ".join(partes).strip()
    final = re.sub(r"<think>.*?</think>", "", "".join(bruto), flags=re.S | re.I).strip()
    if completa and final and not cancelar.is_set():
        await s.enviar(tipo="bot_final", texto=final[:20000], rota=rota)
    if completa:
        s.historico.append({"role": "assistant", "content": completa})
        # Histórico curto: cada turno entra no prompt e atrasa o
        # primeiro token ao longo da conversa.
        if len(s.historico) > 9:
            s.historico[:] = s.historico[:1] + s.historico[-8:]
    elif s.historico and s.historico[-1].get("role") == "user":
        # Turno sem resposta (erro ou interrompido antes da 1ª frase):
        # tira a pergunta órfã, senão o próximo turno responde as duas.
        s.historico.pop()

    await s.enviar(
        tipo="metrica",
        stt=round(t_stt, 2),
        llm=round(t_primeiro_token[0] or 0, 2),
        total=round(primeiro_audio or 0, 2),
        cerebro=rota,
        juiz=round(t_juiz, 2),
        ferramentas=ferramentas,
        cancelado=cancelar.is_set(),
    )
    if rota == "hermes":
        # Registro do que foi para o Claude: os pedidos que se repetem
        # viram comando pronto (src/candidatos.py lista os mais comuns).
        try:
            reg = Path(__file__).resolve().parent.parent / "logs" / "para_claude.jsonl"
            with reg.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"quando": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "texto": texto, "ferramentas": ferramentas,
                                    "segundos": round(time.time() - t_fim_fala, 1),
                                    "resposta": completa[:200]},
                                   ensure_ascii=False) + "\n")
        except OSError:
            pass
    await s.enviar(tipo="estado", estado="ouvindo")


_RE_CANCELAR = re.compile(
    r"^(luna[\s,]+)?(ô |o |ei )?(cancela|cancelar|cancele|cancelado|para|pare|parar|"
    r"chega|esquece|deixa pra la|deixa pra lá|aborta|abortar|stop)"
    r"( tudo| isso| ai| aí| agora| luna)*[\s.!,]*$", re.IGNORECASE)


def _pede_cancelar(texto: str) -> bool:
    return bool(_RE_CANCELAR.match((texto or "").strip().lower()))


async def _barge_in(s: Sessao) -> None:
    """Você falou por cima: cala a resposta e prepara para ouvir o pedido."""
    print("  [barge-in] interrompida pela sua fala", flush=True)
    s.tocando = False
    # Esvazia a fila ANTES de parar o turno: o fim do turno puxa o próximo
    # da fila, e "cancela" rodava um pedido que já devia ter sumido.
    s.fila.clear()
    s.pendente, s.so_nome = None, False
    s.cancelar.set()
    t = s.tarefa
    if t is not None and not t.done():
        t.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(t), 2)
        except BaseException:  # noqa: BLE001 - cancelada/expirou: segue
            pass
    s.ocupado = False
    s.tarefa = None
    await _avisar_fila(s)
    s.falando, s.buf, s.mudo_amostras = False, [], 0
    s.resto = np.zeros(0, dtype=np.float32)
    s.zerar_barge()
    if s.ativacao:
        # A conversa continua: o que você está falando vale sem "Hermes".
        s.acordado_ate = time.time() + 60
        await s.enviar(tipo="acordado")
    await s.enviar(tipo="interrompido", voz=True)
    await s.enviar(tipo="estado", estado="ouvindo")


async def _turno(s: Sessao, audio=None, texto=None, falado=False,
                 imagens=None, forcar=None, contexto=None, nomes=None) -> None:
    """Roda um turno como tarefa, para o comando `interromper` alcançar."""
    s.ocupado = True
    s.pendente, s.so_nome = None, False
    try:
        await _responder(s, audio, texto, falado, imagens, forcar, contexto, nomes)
    except asyncio.CancelledError:
        pass
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        try:
            await s.enviar(tipo="erro", texto=str(e)[:200])
            await s.enviar(tipo="estado", estado="ouvindo")
        except Exception:  # noqa: BLE001
            pass
    finally:
        s.ocupado = False
        s.tarefa = None
        # "Luna" [respiro] "pausa o Spotify": a segunda parte chegou enquanto
        # o nome era transcrito e seria descartada. Vira o próximo turno.
        pend, s.pendente = s.pendente, None
        if s.so_nome and pend is not None and not s.fila:
            print("  [ativacao] pedido emendado ao nome", flush=True)
            s.ocupado = True
            s.tarefa = asyncio.create_task(_turno(s, audio=pend))
        elif s.fila:
            item = s.fila.pop(0)
            print("  [fila] próximo (%d restando): %s"
                  % (len(s.fila), item["texto"][:60]), flush=True)
            s.ocupado = True
            s.tarefa = asyncio.create_task(
                _turno(s, texto=item["texto"], falado=item["falado"],
                       imagens=item.get("imagens"), forcar=item.get("forcar"),
                       contexto=item.get("contexto"), nomes=item.get("nomes")))
            asyncio.create_task(_avisar_fila(s))


async def _avisar_fila(s: Sessao) -> None:
    try:
        await s.enviar(tipo="fila", itens=[i["texto"] for i in s.fila])
    except Exception:  # noqa: BLE001 - tela fechada
        pass


async def _enfileirar(s: Sessao, texto: str, falado: bool,
                      imagens: list | None = None, forcar: str | None = None,
                      contexto: str | None = None, nomes: list | None = None) -> None:
    """Guarda o pedido para depois do turno atual (máx. 5)."""
    if len(s.fila) >= 5:
        await s.enviar(tipo="aviso", texto="Fila cheia (5). Espere ou diga cancela.")
        return
    s.fila.append({"texto": texto, "falado": falado, "imagens": imagens, "forcar": forcar,
                   "contexto": contexto, "nomes": nomes})
    print("  [fila] +%s (%d na fila)" % (texto[:60], len(s.fila)), flush=True)
    await s.enviar(tipo="voce", texto=texto, digitado=not falado)
    await _avisar_fila(s)


async def _abrir_live(s: Sessao) -> None:
    """Liga o modo Live: áudio nativo, sem STT/TTS locais."""
    from live import SessaoLive

    nome_voz = "Leda"
    if s.voz.startswith("gemini:"):
        nome_voz = s.voz.split(":", 1)[1]

    sl = SessaoLive(voz=nome_voz)
    await sl.abrir()
    s.live = sl

    async def bombear():
        """Repassa os eventos do Gemini para o navegador."""
        try:
            async for tipo, dado in sl.receber():
                if tipo == "audio":
                    await s.ws.send_bytes(dado)
                elif tipo == "voce":
                    await s.enviar(tipo="voce", texto=dado, parcial=True)
                elif tipo == "bot":
                    await s.enviar(tipo="bot", texto=dado)
                elif tipo == "interrompido":
                    # O usuário falou por cima: o cliente descarta o que
                    # ainda não tocou, senão a fala antiga continua
                    # saindo por cima da nova.
                    await s.enviar(tipo="interrompido")
                elif tipo == "fim":
                    await s.enviar(tipo="estado", estado="ouvindo")
        except Exception as e:  # noqa: BLE001
            print("  [live] fluxo encerrado: %s" % str(e)[:100], flush=True)

    s.live_tarefa = asyncio.create_task(bombear())
    print("  [live] sessão aberta (voz %s)" % nome_voz, flush=True)


async def _fechar_live(s: Sessao) -> None:
    if s.live_tarefa is not None:
        s.live_tarefa.cancel()
        s.live_tarefa = None
    if s.live is not None:
        await s.live.fechar()
        s.live = None
        print("  [live] sessão fechada", flush=True)


@app.websocket("/ws")
async def ws_conversa(ws: WebSocket):
    await ws.accept()
    s = Sessao(ws)
    _TELAS[s] = None
    ouvido, _v, _vad, _l = _modelos()
    await s.enviar(tipo="pronto", stt=ouvido.dispositivo,
                   modelo=config.LLM_MODELO, voz=s.voz,
                   velocidade=s.velocidade, vozes=config.vozes_disponiveis(),
                   modelos_claude=config.modelos_expert(),
                   modelo_claude=s.modelo_claude,
                   conversa=s.conversa,

                   )
    await s.enviar(tipo="estado", estado="ouvindo")

    try:
        while True:
            msg = await ws.receive()

            if "text" in msg and msg["text"]:
                cmd = json.loads(msg["text"])
                acao = cmd.get("tipo")

                if acao in ("limpar", "conversa_nova"):
                    import conversas as conv
                    s.historico[:] = s.historico[:1]
                    s.conversa = conv.novo_id()
                    await s.enviar(tipo="conversa", id=s.conversa, titulo="", mensagens=[])
                    await s.enviar(tipo="estado", estado="ouvindo")

                elif acao == "conversa_abrir":
                    # Retoma uma conversa salva: a tela redesenha e o modelo
                    # recebe as últimas mensagens como contexto.
                    import conversas as conv
                    c = conv.carregar(cmd.get("id") or "")
                    if c:
                        s.conversa = c["id"]
                        s.historico[:] = conv.historico_llm(c, config.SISTEMA)
                        await s.enviar(tipo="conversa", id=c["id"], titulo=c.get("titulo", ""),
                                       mensagens=c.get("mensagens", []))
                    else:
                        await s.enviar(tipo="conversa", id=s.conversa, titulo="", mensagens=[])

                elif acao == "falar":
                    # "Ouvir de novo": fala o texto sem gravar outra mensagem.
                    t = (cmd.get("texto") or "").strip()[:3000]
                    if not t or s.ocupado:
                        continue
                    from fala import frases as _frases
                    s.ocupado = True
                    try:
                        await s.enviar(tipo="estado", estado="falando")
                        for fr in _frases(iter([t])):
                            res = await asyncio.to_thread(_sintetizar, fr, s.voz, s.velocidade)
                            if res is not None:
                                pcm, taxa = res
                                await s.enviar(tipo="taxa", hz=int(taxa))
                                await s.ws.send_bytes(pcm.tobytes())
                        await s.enviar(tipo="estado", estado="ouvindo")
                    finally:
                        s.ocupado = False

                elif acao == "expert":
                    # "Pedir ao Expert": a mesma pergunta, desta vez direto
                    # ao Claude (sem trocar o modo da tela).
                    t = (cmd.get("texto") or "").strip()[:4000]
                    if not t:
                        continue
                    if not await asyncio.to_thread(_hermes_online):
                        await s.enviar(tipo="erro", texto="Expert indisponível: Claude fora do ar")
                        continue
                    if s.tarefa is not None and not s.tarefa.done():
                        await _enfileirar(s, t, falado=False, forcar="hermes")
                        continue
                    s.tarefa = asyncio.create_task(_turno(s, texto=t, forcar="hermes"))

                elif acao == "interromper":
                    # Esc / botão: para a geração e a fala. O cliente
                    # já cortou o áudio local; aqui paramos a fonte.
                    s.cancelar.set()
                    s.fila.clear()
                    await _avisar_fila(s)
                    print("  [interromper] turno cancelado", flush=True)
                    await s.enviar(tipo="interrompido")

                elif acao == "texto":
                    # Comando digitado: mesmo caminho da fala, sem STT.
                    # Útil para caminhos e nomes que o Whisper erra.
                    t = (cmd.get("texto") or "").strip()[:4000]
                    imagens = [u for u in (cmd.get("imagens") or [])
                               if isinstance(u, str) and u.startswith("data:image/")
                               and len(u) < 8_000_000][:4] or None
                    arquivos = [a for a in (cmd.get("arquivos") or [])
                                if isinstance(a, dict) and a.get("dados")][:3]
                    if imagens and not t:
                        t = "O que tem nessa imagem?"
                    if arquivos:
                        import anexos
                        lidos = [x for x in await asyncio.to_thread(
                            lambda: [anexos.ler(a) for a in arquivos]) if x]
                        if not lidos:
                            await s.enviar(tipo="erro", texto="Não consegui ler esse arquivo.")
                            continue
                        nomes = [x["nome"] for x in lidos]
                        pergunta = t or "Resuma o arquivo."
                        s.arquivos_pendentes = nomes
                        # Na tela aparece só o pedido + nomes; o texto do
                        # arquivo vai ao Claude, que lida melhor com documento.
                        t_llm = anexos.montar_pergunta(pergunta, lidos)
                        if s.tarefa is not None and not s.tarefa.done():
                            await _enfileirar(s, pergunta, falado=False, forcar="hermes",
                                              contexto=t_llm, nomes=nomes)
                            continue
                        s.tarefa = asyncio.create_task(_turno(
                            s, texto=pergunta, forcar="hermes", contexto=t_llm, nomes=nomes))
                        continue
                    if not t:
                        continue
                    if imagens:
                        import conversas as conv
                        s.fotos_pendentes = [u for u in (conv.guardar_imagem(x) for x in imagens) if u]
                    if s.tarefa is not None and not s.tarefa.done():
                        # Ocupada: entra na fila (antes cancelava o turno).
                        if _pede_cancelar(t):
                            await _barge_in(s)
                            await _falar_uma(s, "Cancelei.")
                            await s.enviar(tipo="estado", estado="ouvindo")
                        else:
                            await _enfileirar(s, t, falado=False, imagens=imagens)
                        continue
                    s.tarefa = asyncio.create_task(_turno(s, texto=t, imagens=imagens))

                elif acao == "voz":
                    escolha = cmd.get("voz") or ""
                    validas = {v["id"] for v in config.vozes_disponiveis()}
                    if escolha in validas:
                        s.voz = escolha
                        # RVC sob demanda: liga ao escolher uma convertida
                        # (em segundo plano, para a tela não travar ~20 s)
                        # e desliga ao sair delas.
                        if escolha.startswith("mix:"):
                            await s.enviar(tipo="sistema",
                                           texto="Ligando a voz convertida (RVC): uns 20 s na primeira vez.")
                            asyncio.get_running_loop().run_in_executor(None, _rvc_ligar)
                        elif _rvc_no_ar():
                            asyncio.get_running_loop().run_in_executor(None, _rvc_desligar)
                    elif escolha:
                        # Antes isso era ignorado em silêncio: o cliente
                        # achava que trocou e o servidor seguia na voz
                        # anterior — exatamente o sintoma de "escolhi
                        # Antônio e saiu a Dora".
                        print("  [voz] desconhecida: %r (mantendo %s)"
                              % (escolha[:60], s.voz), flush=True)
                        await s.enviar(tipo="erro",
                                       texto="voz desconhecida: %s" % escolha[:60])
                    vel = cmd.get("velocidade")
                    if isinstance(vel, (int, float)) and 0.5 <= vel <= 2.0:
                        s.velocidade = float(vel)
                    await s.enviar(tipo="voz", voz=s.voz, velocidade=s.velocidade)

                elif acao == "cerebro":
                    # Troca entre o modelo local (rápido, só conversa) e
                    # o Hermes (lento, mas controla o computador).
                    quer = cmd.get("cerebro")
                    if quer == "auto":
                        s.cerebro = "auto"
                    elif quer == "hermes":
                        import urllib.request

                        try:
                            req = urllib.request.Request(
                                config.HERMES_URL + "/models",
                                headers={"Authorization": "Bearer %s"
                                         % config.chave_hermes()})
                            urllib.request.urlopen(req, timeout=8).read()
                            s.cerebro = "hermes"
                        except Exception as e:  # noqa: BLE001
                            print("  [cerebro] Hermes indisponível: %s"
                                  % str(e)[:90], flush=True)
                            await s.enviar(
                                tipo="erro",
                                texto="Hermes fora do ar — rode: hermes gateway run")
                            s.cerebro = "rapido"
                    else:
                        s.cerebro = "rapido"
                    print("  [cerebro] %s" % s.cerebro, flush=True)
                    await s.enviar(tipo="cerebro", cerebro=s.cerebro)

                elif acao == "modelo_claude":
                    # Qual Claude atende o cérebro Hermes: Opus, Fable,
                    # Sonnet, Haiku... ("" volta ao padrão do perfil).
                    escolha = (cmd.get("modelo") or "").strip()
                    if config.modelo_claude_valido(escolha):
                        s.modelo_claude = escolha
                        print("  [modelo-claude] %s" % (escolha or "padrão"),
                              flush=True)
                    else:
                        print("  [modelo-claude] desconhecido: %r (mantendo %s)"
                              % (escolha[:60], s.modelo_claude or "padrão"),
                              flush=True)
                        await s.enviar(tipo="erro",
                                       texto="modelo desconhecido: %s" % escolha[:60])
                    await s.enviar(tipo="modelo_claude", modelo=s.modelo_claude)

                elif acao == "ativacao":
                    import ativacao

                    s.ativacao = bool(cmd.get("ligada"))
                    s.acordado_ate = 0.0
                    print("  [ativacao] %s" % ("ligada" if s.ativacao else "desligada"),
                          flush=True)
                    await s.enviar(tipo="ativacao", ligada=s.ativacao,
                                   janela=ativacao.JANELA_S)

                elif acao == "tocando":
                    # O navegador começou a tocar a resposta.
                    s.tocando = True
                    s.zerar_barge()

                elif acao == "continua":
                    s.continua = bool(cmd.get("ligada"))
                    s.acordado_ate = 0.0
                    print("  [ativacao] conversa continua %s"
                          % ("ligada" if s.continua else "desligada"), flush=True)

                elif acao == "barge":
                    s.barge = bool(cmd.get("ligado", True))
                    print("  [barge-in] %s" % ("ligado" if s.barge else "desligado"),
                          flush=True)

                elif acao == "fim_audio":
                    s.tocando = False
                    s.zerar_barge()
                    # A resposta terminou de tocar: abre a janela para
                    # emendar outro pedido sem repetir "Hermes". Enquanto o
                    # turno ainda roda (ex.: tocou só o "Deixa eu pensar" e o
                    # Claude ainda está pensando) a janela fica aberta.
                    if not s.continua:
                        s.acordado_ate = 0.0
                        await s.enviar(tipo="acordado", ate=0)
                    elif (s.ativacao and not s.ocupado
                            and time.time() < s.acordado_ate):
                        import ativacao

                        s.acordado_ate = time.time() + ativacao.JANELA_S
                        await s.enviar(tipo="acordado", ate=ativacao.JANELA_S)

                elif acao == "modo":
                    # Alterna entre pipeline (STT+LLM+TTS locais) e Live
                    # (áudio nativo do Gemini). Trocar fecha a sessão
                    # anterior: são protocolos diferentes.
                    quer = cmd.get("modo")
                    try:
                        if quer == "live" and s.live is None:
                            await _abrir_live(s)
                        elif quer != "live" and s.live is not None:
                            await _fechar_live(s)
                        await s.enviar(tipo="modo",
                                       modo="live" if s.live else "pipeline")
                        await s.enviar(tipo="estado", estado="ouvindo")
                    except Exception as e:  # noqa: BLE001
                        print("  [live] falhou ao abrir: %s" % str(e)[:150],
                              flush=True)
                        await s.enviar(tipo="erro",
                                       texto="Live indisponível: %s" % str(e)[:120])
                        await s.enviar(tipo="modo", modo="pipeline")

                elif acao == "provar":
                    # Prévia: sintetiza uma frase curta com a voz pedida
                    # para o usuário ouvir antes de adotar.
                    escolha = cmd.get("voz") or s.voz
                    validas = {v["id"] for v in config.vozes_disponiveis()}
                    if escolha not in validas:
                        print("  [provar] voz desconhecida: %r" % escolha[:60],
                              flush=True)
                        await s.enviar(tipo="erro",
                                       texto="voz desconhecida: %s" % escolha[:60])
                        continue
                    vel = cmd.get("velocidade") or s.velocidade
                    texto = (cmd.get("texto")
                             or "Oi! É assim que eu vou falar com você.")
                    if s.ocupado:
                        continue
                    s.ocupado = True
                    try:
                        print("  [provar] %s" % escolha, flush=True)
                        await s.enviar(tipo="estado", estado="falando")
                        res = await asyncio.to_thread(
                            _sintetizar, texto, escolha, vel)
                        if res is not None:
                            pcm, taxa = res
                            await s.enviar(tipo="taxa", hz=int(taxa))
                            await s.ws.send_bytes(pcm.tobytes())
                        await s.enviar(tipo="estado", estado="ouvindo")
                    finally:
                        s.ocupado = False
                continue

            if "bytes" not in msg or msg["bytes"] is None:
                continue

            # --- modo Live: o áudio vai direto ao Gemini --------------
            # Sem VAD local, sem Whisper: o servidor do Google detecta o
            # fim da fala e responde. É por isso que o modo é 2,5x mais
            # rápido — não há três etapas encadeadas.
            if s.live is not None:
                try:
                    await s.live.enviar_audio(msg["bytes"])
                except Exception as e:  # noqa: BLE001
                    print("  [live] envio falhou: %s" % str(e)[:80], flush=True)
                    await s.enviar(tipo="erro", texto="Live caiu; volte ao modo local")
                    await _fechar_live(s)
                    await s.enviar(tipo="modo", modo="pipeline")
                continue

            pcm = np.frombuffer(msg["bytes"], dtype=np.int16)
            if not len(pcm):
                continue
            audio = pcm.astype(np.float32) / 32768.0

            # Ela está falando: só escuta para ser interrompida. Fala firme
            # por cima corta a resposta e vira o próximo pedido (sem perder
            # o começo, guardado em barge_buf). Enquanto ela PENSA ou
            # executa (sem áudio tocando) nada interrompe - uma ação do
            # Claude não fica pela metade.
            if s.tocando:
                if s.barge and await asyncio.to_thread(s.detectar_barge, audio):
                    inicio = np.concatenate(s.barge_buf)
                    await _barge_in(s)
                    completa = await asyncio.to_thread(s.alimentar, inicio)
                    if completa is None:
                        continue
                    s.ocupado = True
                    await s.enviar(tipo="estado", estado="transcrevendo")
                    s.tarefa = asyncio.create_task(_turno(s, audio=completa))
                continue

            # Pensando/executando sem áudio tocando: ignora a conversa, mas
            # escuta "cancela"/"para" (frase curta). Antes todo áudio era
            # jogado fora aqui e o pedido de cancelar nunca chegava.
            if s.ocupado:
                if s.tarefa is None or s.tarefa.done():
                    continue
                curta = await asyncio.to_thread(s.alimentar, audio)
                if curta is None:
                    continue
                if s.so_nome:
                    # Turno era só o nome: o pedido emenda (ver _turno).
                    s.pendente = curta if s.pendente is None else np.concatenate(
                        [s.pendente, curta])
                    continue
                dica = _atualizado("ativacao").DICA if s.ativacao else ""
                txt, _ = await asyncio.to_thread(_transcrever, curta, dica)
                if not txt:
                    continue
                if len(curta) <= 3 * config.SAMPLE_RATE and _pede_cancelar(txt):
                    s.pendente, s.so_nome = None, False
                    print("  [cancelar] por voz durante o turno: %r" % txt, flush=True)
                    await _barge_in(s)
                    await _falar_uma(s, "Cancelei.")
                    await s.enviar(tipo="estado", estado="ouvindo")
                    continue
                # Só entra na fila o que é com ela: chamou pelo nome, ou
                # estamos em conversa (TV e gente em volta ficam de fora).
                if s.ativacao:
                    ativ = _atualizado("ativacao")
                    chamou, pedido = ativ.detectar(txt)
                    if chamou:
                        txt = pedido
                    else:
                        print("  [fila] ignorado (sem o nome): %s" % txt[:60], flush=True)
                        continue
                    if not txt or ativ.encerra(txt):
                        continue
                await _enfileirar(s, txt, falado=True)
                continue

            completa = await asyncio.to_thread(s.alimentar, audio)
            if completa is not None:
                s.ocupado = True
                # Chamada por nome ligada e ninguém chamou ainda: fica
                # quieta até confirmar o "Luna" (portão + texto). Antes o
                # globo ficava âmbar a cada fala da TV/caixa e voltava.
                if not s.ativacao or _acordada(s):
                    await s.enviar(tipo="estado", estado="transcrevendo")
                # Em tarefa separada: o laço continua lendo comandos
                # (interromper, texto) enquanto o turno roda.
                s.tarefa = asyncio.create_task(_turno(s, audio=completa))

    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        try:
            await s.enviar(tipo="erro", texto=str(e)[:200])
        except Exception:
            pass
    finally:
        _TELAS.pop(s, None)
        s.cancelar.set()
        if s.tarefa is not None:
            s.tarefa.cancel()
        # A sessão Live é cobrada por minuto: deixá-la aberta depois que
        # a aba fecha queima crédito sem ninguém ouvindo.
        await _fechar_live(s)


if WEB.exists():
    app.mount("/web", StaticFiles(directory=str(WEB)), name="web")


if __name__ == "__main__":
    import uvicorn

    # Substituto de um /reiniciar: espera o servidor antigo liberar a porta.
    _espera = int(os.environ.pop("REINICIO_ESPERA_PID", "0") or 0)
    if _espera:
        import socket

        _fim = time.time() + 20
        while time.time() < _fim:
            with socket.socket() as _s:
                if _s.connect_ex(("127.0.0.1", 8777)) != 0:
                    break
            time.sleep(0.3)

    print()
    print("=" * 58)
    print("  Assistente de voz  →  http://127.0.0.1:8777")
    print("=" * 58)
    print()
    uvicorn.run(app, host="127.0.0.1", port=8777, log_level="warning")
