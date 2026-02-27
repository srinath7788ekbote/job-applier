# setup_windows_task.ps1
# Creates a Windows Task Scheduler job that runs the pipeline daily at 9 AM.
# Run once as Administrator (or with Task Scheduler write access).
#
# Usage: powershell -ExecutionPolicy Bypass -File setup_windows_task.ps1

$TaskName    = "JobApplierPipeline"
$PipelineDir = "$HOME\.openclaw\workspace\job-applier"
$PythonPath  = (Get-Command python).Source   # uses the python on your PATH
$ScriptPath  = "$PipelineDir\main_pipeline.py"
$LogDir      = "$PipelineDir\logs"

# Create logs directory if needed
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Build the action
$Action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $PipelineDir

# Trigger: daily at 9:00 AM
$Trigger = New-ScheduledTaskTrigger -Daily -At "09:00"

# Settings
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

# Register (replace if exists)
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -RunLevel Highest `
    -Description "Daily job application pipeline (job-applier)"

Write-Host "Task '$TaskName' registered. It will run daily at 9:00 AM."
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To check status:    Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
