@echo off
REM Luna - clique duas vezes para abrir.
REM Na primeira vez instala tudo (Python, dependencias, atalho "Luna");
REM depois so abre. Pode usar o atalho "Luna" da area de trabalho.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
    if errorlevel 1 (
        echo.
        echo   A instalacao nao terminou. Veja a mensagem acima.
        pause
        exit /b 1
    )
)
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File "%~dp0iniciar.ps1"
endlocal
