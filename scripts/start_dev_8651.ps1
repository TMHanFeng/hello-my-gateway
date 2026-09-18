# Model Gateway 开发/测试实例启动器（仅 8651 端口，绝不触碰 8650 生产）
# 用法：右键"使用 PowerShell 运行"，或 .\start_dev_8651.ps1 [-Foreground]
# 行为：强制 MODEL_GATEWAY_PORT=8651 / MODEL_GATEWAY_HOST=127.0.0.1 后台启动，
#       健康检查通过后写 data\dev8651.pid；-Foreground 则前台运行便于看日志。
param([switch]$Foreground)
$ErrorActionPreference = 'Stop'
$dir = if (Test-Path (Join-Path $PSScriptRoot 'app')) { $PSScriptRoot } else { Split-Path -Parent $PSScriptRoot }
$py  = 'D:\miniconda\python.exe'
$base = 'http://127.0.0.1:8651'

if (Test-Path (Join-Path $dir 'data\dev8651.pid')) {
    $old = Get-Content (Join-Path $dir 'data\dev8651.pid') -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
        Write-Host "[dev8651] 已在运行（PID $old，$base）"
        exit 0
    }
}

$env:MODEL_GATEWAY_PORT = '8651'
$env:MODEL_GATEWAY_HOST = '127.0.0.1'

if ($Foreground) {
    Set-Location $dir
    & $py -m app.main
    exit $LASTEXITCODE
}

Write-Host '[dev8651] 正在后台启动（端口 8651，仅监听 127.0.0.1）...'
New-Item -ItemType Directory -Force -Path (Join-Path $dir 'logs') | Out-Null
$out = Join-Path $dir 'logs\dev8651_stdout.log'
$err = Join-Path $dir 'logs\dev8651_stderr.log'
$proc = Start-Process -FilePath $py -ArgumentList '-m', 'app.main' -WorkingDirectory $dir `
        -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru

function Test-Health {
    try {
        $r = Invoke-WebRequest -Uri "$base/health" -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

for ($i = 1; $i -le 45; $i++) {
    if (Test-Health) {
        New-Item -ItemType Directory -Force -Path (Join-Path $dir 'data') | Out-Null
        Set-Content -Path (Join-Path $dir 'data\dev8651.pid') -Value $proc.Id
        Write-Host "[dev8651] 启动成功（PID $($proc.Id)）：$base/admin/  $base/hfadmin"
        Write-Host '[dev8651] 停止：运行根目录 stop_dev_8651.ps1（只停本实例，不碰 8650）'
        exit 0
    }
    Start-Sleep -Seconds 1
}
Write-Host "[dev8651] 45 秒内未通过健康检查，日志：$err"
exit 1
