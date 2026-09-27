# Luna - inicia tudo com um clique (atalho "Luna" / Luna.bat).
#
# Ordem: Hermes (API 8642) -> servidor (8777). O RVC (8778) liga sob demanda.
# -> abre a janela. Cada parte so e' iniciada se ainda nao estiver no ar,
# entao clicar duas vezes no atalho nao duplica nada.
#
# ASCII de proposito: o PowerShell 5 le .ps1 sem BOM como ANSI e
# quebraria acentos. O caminho do projeto vem de $PSScriptRoot.

$ErrorActionPreference = 'Continue'
$Host.UI.RawUI.WindowTitle = 'Luna'
$raiz = $PSScriptRoot
$logs = Join-Path $raiz 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null

function No-Ar($porta) {
    [bool](Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue)
}
function Passo($texto) { Write-Host ('  ' + $texto) -ForegroundColor Gray }
function Ok($texto)    { Write-Host ('  OK  ' + $texto) -ForegroundColor Green }
function Aviso($texto) { Write-Host ('  !!  ' + $texto) -ForegroundColor Yellow }

Write-Host ''
Write-Host '  Luna' -ForegroundColor White
Write-Host '  ----' -ForegroundColor DarkGray

# Sem instalacao ainda: instala antes (primeira vez).
if (-not (Test-Path (Join-Path $raiz '.venv\Scripts\python.exe'))) {
    Write-Host '  primeira vez: instalando (demora alguns minutos)...' -ForegroundColor Yellow
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $raiz 'instalar.ps1')
    if ($LASTEXITCODE -ne 0) { Read-Host '  Enter para fechar'; exit 1 }
}

# Modelos e caches ficam dentro da pasta do projeto (nao enchem o C:).
$env:HF_HOME = Join-Path $raiz 'modelos\hf'
$env:HUGGINGFACE_HUB_CACHE = Join-Path $raiz 'modelos\hf\hub'
$env:TORCH_HOME = Join-Path $raiz 'modelos\torch'
$env:XDG_CACHE_HOME = Join-Path $raiz 'cache'
$env:HF_HUB_DISABLE_XET = '1'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
$env:PYTHONWARNINGS = 'ignore::FutureWarning,ignore::UserWarning'
$env:PYTHONIOENCODING = 'utf-8'   # log em arquivo: sem isso o banner quebra em cp1252
$env:PYTHONUTF8 = '1'
$env:FOR_DISABLE_CONSOLE_CTRL_HANDLER = '1'   # MKL/Fortran: nao morrer quando o console que o abriu fecha

# ------------------------------------------------------------ 1. Cerebro rapido
# DeepSeek pela internet (chave DEEPSEEK_API_KEY no .env). O LM Studio saiu:
# a placa de video fica so com Whisper e vozes.
$envArq = Join-Path $PSScriptRoot '.env'
if ((Test-Path $envArq) -and (Select-String -Path $envArq -Pattern '^DEEPSEEK_API_KEY=.{20,}' -Quiet)) {
    Ok 'cerebro rapido: DeepSeek'
} else {
    Aviso 'falta a chave do DeepSeek - a tela de Primeiros passos vai pedir'
}

# ------------------------------------------------------------ 2. Hermes
$hermes = Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\venv\Scripts\hermes.exe'
if (No-Ar 8642) {
    Ok 'Hermes ja estava no ar'
} elseif (Test-Path $hermes) {
    Passo 'ligando o Hermes (controle do computador)...'
    $logH = Join-Path $env:LOCALAPPDATA 'hermes\gateway-voz.log'
    Start-Process -FilePath $hermes -ArgumentList '-p', 'default', 'gateway', 'run' `
        -WindowStyle Hidden -RedirectStandardOutput $logH -RedirectStandardError ($logH + '.err')
} else {
    Passo 'Hermes nao instalado (opcional) - sem modo Expert'
}

# ------------------------------------------------------------ 3. RVC
# Nao sobe aqui: as vozes convertidas (RVC) sao opcionais e pesadas, e o
# servidor liga o servico sozinho quando uma delas e escolhida.

# ------------------------------------------------------------ 4. servidor
$py = Join-Path $raiz '.venv\Scripts\python.exe'
if (No-Ar 8777) {
    Ok 'assistente ja estava no ar'
} else {
    Passo 'ligando o assistente (os modelos levam ~25s)...'
    Start-Process -FilePath $py -ArgumentList ('"' + (Join-Path $raiz 'src\servidor.py') + '"') `
        -WorkingDirectory $raiz -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'srv.log') -RedirectStandardError (Join-Path $logs 'srv.err.log')
}

# ------------------------------------------------------------ espera
$limite = (Get-Date).AddSeconds(150)
$pronto = $false
while ((Get-Date) -lt $limite) {
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8777/estado' -TimeoutSec 3 | Out-Null
        $pronto = $true; break
    } catch { Start-Sleep -Milliseconds 1500 }
}
if (-not $pronto) {
    Aviso 'o assistente nao respondeu em 150s. Veja logs\srv.err.log'
    Read-Host '  Enter para fechar'
    exit 1
}
Ok 'assistente pronto'
foreach ($p in @(@(8642, 'Hermes'))) {
    $fim = (Get-Date).AddSeconds(40)
    while (-not (No-Ar $p[0]) -and (Get-Date) -lt $fim) { Start-Sleep -Milliseconds 1000 }
    if (No-Ar $p[0]) { Ok ($p[1] + ' pronto') } else { Aviso ($p[1] + ' ainda subindo - aparece sozinho na tela quando ficar pronto') }
}

# ------------------------------------------------------------ janela
# Chrome/Edge em modo app: janela propria, sem abas nem barra de endereco.
$url = 'http://127.0.0.1:8777'
$navs = @(
    (Join-Path ${env:ProgramFiles} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path ${env:ProgramFiles} 'Microsoft\Edge\Application\msedge.exe')
)
$nav = $navs | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($nav) {
    # Perfil proprio do assistente (no L:), separado do Chrome normal:
    #  - autoplay-policy: toca a voz sem precisar de clique antes;
    #  - a permissao do microfone fica salva neste perfil (Preferences),
    #    entao so e pedida uma vez - sem flag que gera aviso de seguranca.
    # Assim, com "Chamar por Hermes" ligado, basta abrir e falar.
    $perfil = Join-Path $PSScriptRoot 'navegador'
    Start-Process -FilePath $nav -ArgumentList @(
        ('--app=' + $url), '--window-size=1280,800',
        ('--user-data-dir="' + $perfil + '"'),
        '--autoplay-policy=no-user-gesture-required',
        '--no-first-run', '--no-default-browser-check')
} else {
    Start-Process $url
}
Start-Sleep -Seconds 2
