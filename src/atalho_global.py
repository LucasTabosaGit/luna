"""Atalho global para chamar a Luna de qualquer lugar.

    Ctrl + Alt + L  ->  traz a janela da Luna para a frente (abre se estiver
                        fechada) e já começa a ouvir, como dizer "Luna".

Cada sistema tem o seu (escolhido sozinho aqui):
- Windows: src/atalho_global_win.py (RegisterHotKey: atalho registrado no
  Windows, NÃO um hook de teclado; não lê o que você digita).
- Mac: src/atalho_global_mac.py (pynput; precisa da permissão de
  Acessibilidade uma vez).
"""
import sys

if sys.platform == "darwin":
    import atalho_global_mac as _m
else:
    import atalho_global_win as _m

DESCRICAO = _m.DESCRICAO
iniciar = _m.iniciar
parar = _m.parar
trazer_para_frente = _m.trazer_para_frente
