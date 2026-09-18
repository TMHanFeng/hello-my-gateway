# Model Gateway silent starter (for autostart / background). Visible window: use root start_gateway.cmd
$ErrorActionPreference = 'Stop'
$dir = if (Test-Path (Join-Path $PSScriptRoot 'app')) { $PSScriptRoot } else { Split-Path -Parent $PSScriptRoot }
$py  = 'D:\miniconda\python.exe'
if (Test-Path (Join-Path $dir '.venv-headroom\Scripts\python.exe')) { $py = Join-Path $dir '.venv-headroom\Scripts\python.exe' }
$base = 'http://127.0.0.1:8650'

function Test-Health {
    try {
        $r = Invoke-WebRequest -Uri "$base/health" -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

if (Test-Health) {
    Write-Host '[Model Gateway] Already running, nothing to do.'
    exit 0
}

Write-Host '[Model Gateway] Starting silently...'
New-Item -ItemType Directory -Force -Path (Join-Path $dir 'logs') | Out-Null
$out = Join-Path $dir 'logs\gateway_stdout.log'
$err = Join-Path $dir 'logs\gateway_stderr.log'
$proc = Start-Process -FilePath $py -ArgumentList '-m', 'app.main' -WorkingDirectory $dir `
        -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru

for ($i = 1; $i -le 60; $i++) {
    if (Test-Health) {
        Set-Content -Path (Join-Path $dir 'gateway.pid') -Value $proc.Id
        Write-Host "[Model Gateway] Started (PID $($proc.Id)), running hidden on 8650."
        exit 0
    }
    Start-Sleep -Seconds 1
}
Write-Host "[Model Gateway] Health check failed in 60s. Log: $err"
exit 1
