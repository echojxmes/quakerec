$dir = Split-Path -Parent $PSScriptRoot
$py  = Join-Path $dir ".venv\Scripts\pythonw.exe"
$act = New-ScheduledTaskAction -Execute $py -Argument "quakerec.py" -WorkingDirectory $dir
$trg = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "quakerec" -Action $act -Trigger $trg -RunLevel Limited -Force