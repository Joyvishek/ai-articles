param(
  [string]$TaskName = "Daily AI Article Digest",
  [string]$Time = "12:00"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python).Source
$Script = Join-Path $ProjectDir "main.py"
$Config = Join-Path $ProjectDir "digest_config.json"

if (-not (Test-Path $Config)) {
  throw "Missing digest_config.json. Copy digest_config.example.json to digest_config.json and fill in your settings first."
}

$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`" --config `"$Config`""
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Sends a daily email digest of new AI articles." -Force
Write-Host "Registered '$TaskName' to run daily at $Time."
