# Cria o atalho "Luna" na area de trabalho e no menu Iniciar.
# Um atalho so: abre a Luna (liga o que faltar). Para desligar, use
# Ajustes -> Desligar a Luna, ou Ctrl+K -> "Desligar".
$raiz = $PSScriptRoot
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$sh = New-Object -ComObject WScript.Shell
$area = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Luna.lnk'
$menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Luna.lnk'

foreach ($destino in @($area, $menu)) {
    $a = $sh.CreateShortcut($destino)
    $a.TargetPath = $ps
    $a.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File "' + (Join-Path $raiz 'iniciar.ps1') + '"'
    $a.WorkingDirectory = $raiz
    $a.IconLocation = (Join-Path $raiz 'web\icone.ico') + ',0'
    $a.Description = 'Abre a Luna, assistente de voz'
    $a.WindowStyle = 7   # minimizado: a janela de progresso nao atrapalha
    $a.Save()
}

# Atalhos antigos (dois botoes) viram um so.
foreach ($velho in 'Assistente de voz.lnk', 'Parar assistente.lnk') {
    $p = Join-Path ([Environment]::GetFolderPath('Desktop')) $velho
    if (Test-Path $p) { Remove-Item $p -ErrorAction SilentlyContinue }
}
Write-Host '  OK  atalho "Luna" na area de trabalho e no menu Iniciar' -ForegroundColor Green
