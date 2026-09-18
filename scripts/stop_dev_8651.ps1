# 停止 8651 开发实例：只读 data\dev8651.pid 精确停止本实例，绝不触碰 8650 生产端口/进程
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $dir 'data\dev8651.pid'
if (-not (Test-Path $pidFile)) { Write-Host '[dev8651] 无 data\dev8651.pid，本实例未在运行'; exit 0 }
$target = Get-Content $pidFile
# 双保险：确认该 PID 确实是监听 8651 的进程才停止
$lines = netstat -ano | Select-String ':8651\s'
$listening = $lines | ForEach-Object { ($_ -split '\s+')[-1] } | Select-Object -Unique
if ($listening -notcontains $target) {
    Write-Host "[dev8651] PID $target 未监听 8651，拒绝停止（防止误伤其他进程）"; Remove-Item $pidFile; exit 1
}
Stop-Process -Id $target -Force
Remove-Item $pidFile
Write-Host "[dev8651] 已停止开发实例（PID $target）。8650 生产不受影响。"
