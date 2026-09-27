# Instala a Luna: .venv, dependencias, .env e atalhos.
# Uso: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
Set-Location $raiz

function Ok($t)    { Write-Host ('  OK  ' + $t) -ForegroundColor Green }
function Passo($t) { Write-Host ('  ..  ' + $t) -ForegroundColor Gray }
function Falha($t) { Write-Host ('  !!  ' + $t) -ForegroundColor Red; exit 1 }

Write-Host ''
Write-Host '  Luna - instalacao' -ForegroundColor White
Write-Host ''

# 1. Python 3.11
$py = $null
foreach ($c in @('py -3.11', 'python3.11', 'python')) {
    try {
        $v = & cmd /c "$c --version 2>&1"
        if ($v -match 'Python 3\.11') { $py = $c; break }
    } catch {}
}
if (-not $py) { Falha 'Python 3.11 nao encontrado. Instale: winget install Python.Python.3.11' }
Ok "Python: $py"

# 2. ffmpeg
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Falha 'ffmpeg nao encontrado. Instale: winget install ffmpeg  (e abra um terminal novo)'
}
Ok 'ffmpeg'

# 3. ambiente
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Passo 'criando .venv...'
    & cmd /c "$py -m venv .venv"
}
$vpy = Join-Path $raiz '.venv\Scripts\python.exe'
Passo 'instalando PyTorch com CUDA (demora)...'
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install torch --index-url https://download.pytorch.org/whl/cu128 --quiet
Passo 'instalando o resto...'
& $vpy -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { Falha 'pip falhou (veja a mensagem acima)' }
Ok 'dependencias'

# 4. .env
if (-not (Test-Path '.env')) { Copy-Item '.env.exemplo' '.env'; Ok '.env criado (a chave voce cola na tela)' }
else { Ok '.env ja existe' }

# 5. atalhos
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $raiz 'criar_atalho.ps1')

Write-Host ''
Write-Host '  Pronto. Abra "Luna" na area de trabalho (ou no menu Iniciar).' -ForegroundColor White
Write-Host '  Na primeira vez os modelos (~3 GB) baixam sozinhos.' -ForegroundColor Gray
Write-Host ''
