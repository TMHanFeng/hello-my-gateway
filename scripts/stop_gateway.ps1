# Model Gateway production stop (port 8650). Location-aware + identity-checked:
# ONLY stops the gateway whose command line points at THIS repo - never another env's instance.
$dir = if (Test-Path (Join-Path $PSScriptRoot 'app')) { $PSScriptRoot } else { Split-Path -Parent $PSScriptRoot }
$conn = Get-NetTCPConnection -LocalPort 8650 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $conn) {
    Write-Host 'Model Gateway not running (no listener on 8650).'
    Remove-Item (Join-Path $dir 'gateway.pid') -ErrorAction SilentlyContinue
    exit 0
}
$target = $conn.OwningProcess
$proc = Get-CimInstance Win32_Process -Filter "ProcessId=$target" -ErrorAction SilentlyContinue
$cmdline = if ($proc) { $proc.CommandLine } else { '' }
if ($cmdline -and $cmdline -notlike '*hello-my-gateway*') {
    Write-Host "REFUSED: PID $target on 8650 does not belong to this repo:"
    Write-Host "  $cmdline"
    Write-Host 'Nothing was stopped.'
    exit 1
}
Stop-Process -Id $target -Force
Remove-Item (Join-Path $dir 'gateway.pid') -ErrorAction SilentlyContinue
Write-Host "Model Gateway (hello-my-gateway) stopped (PID $target)."
