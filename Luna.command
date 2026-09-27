#!/bin/bash
# Luna - clique duas vezes para abrir.
# Na primeira vez instala tudo (instalar.py); depois so abre (src/abrir.py).
# Depois pode usar o atalho "Luna" em Aplicativos ou na area de trabalho.
cd "$(dirname "$0")"

if [ -x ".venv/bin/python3" ]; then
    exec ".venv/bin/python3" "src/abrir.py"
fi

PY=""
for cand in python3.11 /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11; do
    if command -v "$cand" >/dev/null 2>&1; then
        PY="$cand"
        break
    fi
done

if [ -z "$PY" ]; then
    echo
    echo "  Python 3.11 nao encontrado. Instale com:  brew install python@3.11"
    echo "  e clique no Luna.command de novo."
    read -r -p "  Aperte Enter para fechar..." _
    exit 1
fi

if ! "$PY" instalar.py; then
    echo
    echo "  A instalacao nao terminou. Veja a mensagem acima."
    read -r -p "  Aperte Enter para fechar..." _
    exit 1
fi

exec ".venv/bin/python3" "src/abrir.py"
