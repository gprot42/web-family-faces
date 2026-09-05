# Family Faces on Windows (PowerShell): .\scripts\app.ps1 start | stop | restart | status | debug | logs
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "py" ; $args = @("-3.12") + $args }
& $python (Join-Path $root "scripts\app.py") @args
exit $LASTEXITCODE
