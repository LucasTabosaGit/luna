# Assistente de voz - reinicia SO o servidor (porta 8777).
#
# NAO e necessario para comandos novos: o servidor recarrega sozinho
# src\comandos.py, src\juiz.py e src\ativacao.py quando o arquivo muda.
# Use so depois de mexer no servidor.py, config.py ou nos modelos.
#
# Como funciona: com o servidor no ar, pede a ELE que se reinicie
# (POST /reiniciar). O proprio servidor lanca o substituto e sai. Isso
# importa porque quem pede costuma ser o Hermes numa conversa que passa
# pelo servidor: se este script derrubasse a porta e tentasse religar,
# morria junto com a conversa e ninguem religava (aconteceu).
#
# Nao mexe no Hermes (8642) nem no RVC (8778); a
# janela do navegador reconecta sozinha.
#
# ASCII de proposito: o PowerShell 5 le .ps1 sem BOM como ANSI.

$ErrorActionPreference = 'Continue'
$raiz = $PSScriptRoot

function No-Ar($porta) {
    $t = New-Object Net.Sockets.TcpClient
    try { $t.Connect('127.0.0.1', $porta); $true } catch { $false } finally { $t.Close() }
}

Write-Host ''
Write-Host '  Reiniciando o assistente' -ForegroundColor White

if (No-Ar 8777) {
    try {
        Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8777/reiniciar' -TimeoutSec 10 | Out-Null
        Write-Host '  OK  reinicio pedido: o assistente volta sozinho em ~30s' -ForegroundColor Green
        Write-Host '      (a janela reconecta; comandos novos nem precisam disso)'
        exit 0
    } catch {
        Write-Host ('  !!  o servidor nao aceitou o pedido: ' + $_.Exception.Message) -ForegroundColor Yellow
        Write-Host '      use o atalho Parar assistente e depois Assistente de voz'
        exit 1
    }
}

# Fora do ar: liga pelo caminho normal (mesmo ambiente do atalho).
Write-Host '  nao estava no ar - ligando'
& (Join-Path $raiz 'iniciar.ps1') -SemJanela
