@echo off
chcp 65001 >nul
REM ============================================================
REM  Hello My Gateway - open firewall for Quota Refresh port 8655
REM  Right-click this file -> "Run as administrator"
REM  (Self-elevates via UAC if not run as admin.)
REM ============================================================
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo [i] Need administrator privilege, requesting elevation (UAC) ...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
echo [1/2] Removing old rule with same name (if any) ...
netsh advfirewall firewall delete rule name="HelloGateway Quota Refresh 8655" >nul 2>&1
echo [2/2] Adding inbound allow rule: TCP 8655 ...
netsh advfirewall firewall add rule name="HelloGateway Quota Refresh 8655" dir=in action=allow protocol=TCP localport=8655
if %errorlevel% equ 0 (
  echo.
  echo [OK] Port 8655 is now open on Windows Firewall (all profiles).
  echo      LAN devices can now reach: http://THIS-PC-IP:8655/refresh
) else (
  echo.
  echo [FAIL] Rule not added. Check that Windows Firewall service is running.
)
echo.
pause
