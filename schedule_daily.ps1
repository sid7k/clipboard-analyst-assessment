param(
    [string]$Time = "06:00"
)

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $projectDir "run_daily.bat"
$taskName = "Bellhaven CRM Daily Reconciliation"

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$runner`""

$trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At $Time

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Description "Daily Bellhaven reconciliation refresh. Generates review proposals but does not auto-approve CRM changes." `
    -Force

Write-Host ""
Write-Host "Scheduled task created:"
Write-Host $taskName
Write-Host "Runs daily at $Time"