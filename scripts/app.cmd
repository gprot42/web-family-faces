@echo off
rem Family Faces on Windows: app.cmd start | stop | restart | status | debug | logs
setlocal
set "ROOT=%~dp0.."
if exist "%ROOT%\.venv\Scripts\python.exe" (
  "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\scripts\app.py" %*
) else (
  py -3.12 "%ROOT%\scripts\app.py" %*
)
endlocal
