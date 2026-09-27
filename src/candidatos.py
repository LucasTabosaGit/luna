"""Lista os pedidos que foram para o Claude e ainda não são comando pronto.

    .venv/Scripts/python.exe src/candidatos.py

Lê logs/para_claude.jsonl, agrupa frases parecidas e mostra as mais
frequentes. As que se repetem e são ações simples (abrir, ligar, mostrar)
são candidatas a virar regra em src/comandos.py - aí passam a rodar em
~0,2s em vez de ~10s.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comandos  # noqa: E402

REG = Path(__file__).resolve().parent.parent / "logs" / "para_claude.jsonl"


def main() -> int:
    if not REG.exists():
        print("Nenhum pedido registrado ainda.")
        return 0
    linhas = [json.loads(x) for x in REG.read_text(encoding="utf-8").splitlines() if x.strip()]
    grupos: Counter = Counter()
    exemplo, tempo = {}, {}
    for r in linhas:
        chave = comandos.normalizar(r["texto"])
        grupos[chave] += 1
        exemplo.setdefault(chave, r["texto"])
        tempo.setdefault(chave, []).append(r.get("segundos", 0))
    print(f"{len(linhas)} pedidos foram para o Claude ({len(grupos)} diferentes)\n")
    print(" vezes  média   já é comando?  frase")
    for chave, n in grupos.most_common(40):
        ja = "sim" if comandos.tentar(exemplo[chave], executar=False) else "-"
        media = sum(tempo[chave]) / len(tempo[chave])
        print(f"  {n:4d}  {media:5.1f}s  {ja:13s}  {exemplo[chave]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
