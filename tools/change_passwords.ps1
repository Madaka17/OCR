# Changes the Thai OCR passwords and restarts the server so they take effect.
# Double-click tools\change_passwords.cmd (asks for Administrator), type the new passwords, done.
# Everyone who is logged in must log in again with the new password.
$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

try {
    Set-Location $project
    $env:PYTHONUTF8 = "1"
    Write-Host "เปลี่ยนรหัสผ่าน Thai OCR (ตอนพิมพ์จะไม่เห็นตัวอักษร)" -ForegroundColor Cyan
    & "$project\.venv\Scripts\python.exe" -m server passwords
    if ($LASTEXITCODE -ne 0) { throw "ตั้งรหัสผ่านไม่สำเร็จ" }

    Write-Host "กำลังเริ่มเซิร์ฟเวอร์ใหม่..." -ForegroundColor Cyan
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*-m server*' -and $_.CommandLine -notlike '*passwords*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Stop-ScheduledTask -TaskName "ThaiOCR Server" -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName "ThaiOCR Server"

    $ok = $false
    foreach ($i in 1..30) {
        Start-Sleep -Seconds 2
        try {
            $r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:7860/health -TimeoutSec 5
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch {}
    }
    if ($ok) { Write-Host "เสร็จแล้ว รหัสใหม่ใช้ได้ทันที" -ForegroundColor Green }
    else { Write-Host "เซิร์ฟเวอร์ยังไม่ตอบ ดู log ที่ $project\data\logs\server.log" -ForegroundColor Yellow }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
}
Read-Host "กด Enter เพื่อปิด"
