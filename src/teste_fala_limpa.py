"""A voz não pode ler símbolos de markdown ("asterisco", "hífen", "barra vertical").

    .venv/Scripts/python.exe src/teste_fala_limpa.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fala import limpar  # noqa: E402

CASOS = [
    ("**Pronto!** Fiz isso.", "Pronto! Fiz isso."),
    ("- Criei o arquivo.", "Criei o arquivo."),
    ("1. Primeiro passo.", "Primeiro passo."),
    ("## Resumo", "Resumo"),
    ("---", ""),
    ("**", ""),
    ("|---|---|", ""),
    ("Veja [o site](https://exemplo.com/a).", "Veja o site."),
    ("Veja https://github.com/x/y", "Veja github.com"),
    ("Em `notas.txt`.", "Em notas.txt."),
    ("~~velho~~ novo", "velho novo"),
    ("2*3 = 6", "2 vezes 3 = 6"),
    ("Olá! 😀", "Olá!"),
    ("A → B", "A, B"),
    ("```py\nprint(1)\n```\nFeito.", "Feito."),
]
SIMBOLOS = set("*|#`~→•")


def main() -> int:
    ok = 0
    for entrada, esperado in CASOS:
        saiu = limpar(entrada, pronuncia=False)
        if saiu == esperado and not (SIMBOLOS & set(saiu)):
            ok += 1
        else:
            print("  FALHOU %r -> %r (esperado %r)" % (entrada, saiu, esperado))
    print("%d/%d corretos" % (ok, len(CASOS)))
    return 0 if ok == len(CASOS) else 1


if __name__ == "__main__":
    sys.exit(main())
