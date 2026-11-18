# Installs the Thai OCR server on this PC. Run once in an elevated (Administrator) PowerShell:
#   powershell -ExecutionPolicy Bypass -File tools\install_server.ps1
#
# - Scheduled task "ThaiOCR Server": starts at boot as SYSTEM (no login needed),
#   restarts every minute on failure, no time limit. Output goes to data\logs\server.log.
# - Firewall rule "ThaiOCR 7860": inbound TCP 7860 only from the Raspberry Pi (Cloudflare Tunnel + Uptime Kuma).
# Run it again to update; it replaces the existing task and rule.
param(
    [string]$PiAddress = "192.168.1.109",
    [int]$Port = 7860
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script in an elevated PowerShell (Run as administrator)."
}

$project = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $project "tools\run_server.cmd"
if (-not (Test-Path (Join-Path $project ".venv\Scripts\python.exe"))) { throw "Missing .venv in $project" }
if (-not (Test-Path (Join-Path $project "data\config.json"))) {
    throw "Set passwords first: .venv\Scripts\python.exe -m server passwords"
}

$taskName = "ThaiOCR Server"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$runner`"" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
$taskUser = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $taskUser -Force | Out-Null
Write-Host "Scheduled task '$taskName' registered."

$ruleName = "ThaiOCR $Port"
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port `
    -RemoteAddress $PiAddress -Action Allow -Profile Any | Out-Null
Write-Host "Firewall rule '$ruleName' allows TCP $Port from $PiAddress."

# An older "python.exe" allow rule lets any address reach every port Python listens on.
# Block rules win over allow rules, so block TCP $Port from every address except the Pi
# (localhost is never filtered). Other Python programs keep working on their own ports.
$blockName = "ThaiOCR $Port block others"
Get-NetFirewallRule -DisplayName $blockName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
$ip = [System.Net.IPAddress]::Parse($PiAddress).GetAddressBytes()
[Array]::Reverse($ip); $n = [BitConverter]::ToUInt32($ip, 0)
function ConvertTo-IPv4([uint32]$v) { $b = [BitConverter]::GetBytes($v); [Array]::Reverse($b); ([System.Net.IPAddress]$b).ToString() }
$ranges = @()
if ($n -gt 0) { $ranges += "0.0.0.0-" + (ConvertTo-IPv4 ($n - 1)) }
if ($n -lt [uint32]::MaxValue) { $ranges += (ConvertTo-IPv4 ($n + 1)) + "-255.255.255.255" }
$ranges += "::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"
New-NetFirewallRule -DisplayName $blockName -Direction Inbound -Protocol TCP -LocalPort $Port `
    -RemoteAddress $ranges -Action Block -Profile Any | Out-Null
Write-Host "Firewall rule '$blockName' blocks TCP $Port from every other address."

Start-ScheduledTask -TaskName $taskName
Write-Host "Started. Check: curl http://127.0.0.1:$Port/health   Log: $project\data\logs\server.log"
