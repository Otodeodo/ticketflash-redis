<#
.SYNOPSIS
  Programa el respaldo diario de TicketFlash en el Programador de tareas de Windows.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\schedule_backup.ps1 -Hora "02:30"

  Ejecuta cada día:  python scripts\backup.py backup  y  python scripts\backup.py verify <último zip>
  (las URL de conexión se leen del archivo .env del proyecto).
  Para quitarla:  Unregister-ScheduledTask -TaskName "TicketFlash-Backup" -Confirm:$false

  En Linux/macOS usa cron:
    30 2 * * * cd /ruta/ticketflash && .venv/bin/python scripts/backup.py backup >> backups/backup.log 2>&1
#>
param(
  [string]$Hora = "02:30",
  [string]$Python = (Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"),
  [string]$TaskName = "TicketFlash-Backup"
)

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path $Python)) { $Python = (Get-Command python).Source }

$cmd = "& '$Python' scripts\backup.py backup *>> backups\backup.log"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -Command `"Set-Location '$root'; New-Item -ItemType Directory -Force backups | Out-Null; $cmd`""
$trigger = New-ScheduledTaskTrigger -Daily -At $Hora
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Tarea '$TaskName' creada: respaldo diario a las $Hora. Registro en backups\backup.log"
