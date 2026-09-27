"""Quanto a Luna gastou hoje no cérebro rápido (para a barra de status).

Estimativa pelos tokens e pelo preço de tabela de cada IA (config.CEREBROS);
o valor exato está no painel de cada serviço.

O cliente OpenAI do cérebro rápido é embrulhado (`embrulhar`): toda chamada
`chat.completions.create` soma os tokens do `usage` num contador do dia,
salvo em dados/custo.json. Streaming pede `include_usage` para o último
pedaço trazer a contagem.

Preço (api-docs.deepseek.com, deepseek-flash, por 1M tokens, em US$):
entrada 0,15 (fora de pico) / 0,30 (pico); cache 0,003/0,006; saída 0,60/1,20.
Pico = 01-04h e 06-10h UTC, seg a sex.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

ARQ = Path(__file__).resolve().parent.parent / "dados" / "custo.json"
PRECO = {"entrada": 0.15, "cache": 0.003, "saida": 0.60}   # fora de pico
DOLAR = 5.40      # conversão aproximada para mostrar em reais
_trava = threading.Lock()


def _pico(agora: dt.datetime) -> bool:
    u = agora.astimezone(dt.timezone.utc)
    return u.weekday() < 5 and (1 <= u.hour < 4 or 6 <= u.hour < 10)


def _ler() -> dict:
    try:
        return json.loads(ARQ.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def somar(usage) -> None:
    if usage is None:
        return
    entrada = getattr(usage, "prompt_tokens", 0) or 0
    saida = getattr(usage, "completion_tokens", 0) or 0
    cache = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    agora = dt.datetime.now().astimezone()
    import config
    c = config.cerebro()
    if c["id"] == "deepseek":
        k = 2.0 if _pico(agora) else 1.0
        usd = k * ((entrada - cache) * PRECO["entrada"] + cache * PRECO["cache"]
                   + saida * PRECO["saida"]) / 1e6
    else:
        p_in, p_out = c["preco"]
        usd = (entrada * p_in + saida * p_out) / 1e6
    hoje = agora.date().isoformat()
    with _trava:
        d = _ler()
        dia = d.setdefault(hoje, {"usd": 0.0, "chamadas": 0, "entrada": 0, "saida": 0})
        dia["usd"] += usd
        dia["chamadas"] += 1
        dia["entrada"] += entrada
        dia["saida"] += saida
        for velho in sorted(d)[:-31]:          # guarda um mês
            d.pop(velho, None)
        ARQ.parent.mkdir(parents=True, exist_ok=True)
        ARQ.write_text(json.dumps(d), encoding="utf-8")


def hoje() -> dict:
    d = _ler()
    dia = d.get(dt.date.today().isoformat(), {"usd": 0.0, "chamadas": 0})
    mes = sum(v.get("usd", 0) for k, v in d.items() if k[:7] == dt.date.today().isoformat()[:7])
    return {"usd": round(dia["usd"], 5), "brl": round(dia["usd"] * DOLAR, 4),
            "chamadas": dia["chamadas"], "mes_brl": round(mes * DOLAR, 3)}


def historico(dias: int = 30) -> list[dict]:
    """Últimos `dias` dias, do mais antigo ao de hoje (dias sem uso = zero)."""
    d = _ler()
    hoje_ = dt.date.today()
    saida = []
    for i in range(dias - 1, -1, -1):
        dia = (hoje_ - dt.timedelta(days=i)).isoformat()
        v = d.get(dia, {})
        saida.append({"dia": dia, "usd": round(v.get("usd", 0.0), 5),
                      "brl": round(v.get("usd", 0.0) * DOLAR, 4), "chamadas": v.get("chamadas", 0),
                      "entrada": v.get("entrada", 0), "saida": v.get("saida", 0)})
    return saida


def estimativas() -> dict:
    """Quanto o SEU uso custaria por mês em cada IA (média dos dias usados).

    Serve para a dica "mais barato": mesmos tokens, preço de cada uma."""
    import config
    usados = [h for h in historico(30) if h["chamadas"]]
    if not usados:
        return {"dias_com_uso": 0, "chamadas_dia": 0, "ias": []}
    n = len(usados)
    ent = sum(h["entrada"] for h in usados) / n
    sai = sum(h["saida"] for h in usados) / n
    ias = []
    for ident, c in config.CEREBROS.items():
        if ident in ("outro",):
            continue
        p_in, p_out = c["preco"]
        if ident == "deepseek":
            p_in, p_out = PRECO["entrada"], PRECO["saida"]
        usd_mes = (ent * p_in + sai * p_out) / 1e6 * 30
        ias.append({"id": ident, "nome": c["nome"], "brl_mes": round(usd_mes * DOLAR, 2),
                    "gratis": ident in ("ollama", "assinatura"),
                    "cota_gratis": ident in ("gemini", "groq")})
    ias.sort(key=lambda x: x["brl_mes"])
    return {"dias_com_uso": n, "chamadas_dia": round(sum(h["chamadas"] for h in usados) / n),
            "ias": ias}


class _Fluxo:
    """Repassa um stream e soma o usage do último pedaço."""

    def __init__(self, fluxo):
        self._f = fluxo

    def __iter__(self):
        for ev in self._f:
            u = getattr(ev, "usage", None)
            if u is not None:
                try:
                    somar(u)
                except Exception:  # noqa: BLE001
                    pass
            yield ev

    def close(self):
        return self._f.close()

    def __getattr__(self, nome):
        return getattr(self._f, nome)


class _Completions:
    def __init__(self, real):
        self._real = real

    def create(self, *a, **kw):
        if kw.get("stream"):
            kw.setdefault("stream_options", {"include_usage": True})
            return _Fluxo(self._real.create(*a, **kw))
        r = self._real.create(*a, **kw)
        try:
            somar(getattr(r, "usage", None))
        except Exception:  # noqa: BLE001
            pass
        return r


class _Chat:
    def __init__(self, real):
        self.completions = _Completions(real.completions)


class Contado:
    """Cliente OpenAI com os mesmos métodos, contando o gasto."""

    def __init__(self, cliente):
        self._c = cliente
        self.chat = _Chat(cliente.chat)

    def __getattr__(self, nome):
        return getattr(self._c, nome)


def embrulhar(cliente):
    return Contado(cliente)
