@echo off
REM Luna - clique duas vezes para abrir.
REM Na primeira vez instala tudo (instalar.py); depois so abre (src\abrir.py).
REM Depois pode usar o atalho "Luna" da area de trabalho.
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" goto abrir

set "PY="
py -3.11 -c "import sys" >nul 2>nul && set "PY=py -3.11"
if not defined PY python -c "import sys; sys.exit(sys.version_info[:2] != (3, 11))" >nul 2>nul && set "PY=python"
if not defined PY (
    echo.
    echo   Python 3.11 nao encontrado. Instale com:  winget install Python.Python.3.11
    echo   e clique no Luna.bat de novo.
    pause
    exit /b 1
)
%PY% instalar.py
if errorlevel 1 (
    echo.
    echo   A instalacao nao terminou. Veja a mensagem acima.
    pause
    exit /b 1
)

:abrir
start "" ".venv\Scripts\pythonw.exe" "src\abrir.py"
endlocal
