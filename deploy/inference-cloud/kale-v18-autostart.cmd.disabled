@echo off
REM Bring the v18 stack up at logon: llama-server, the inference API, and the tunnel.
REM
REM Installed by copying (or shortcutting) this file into the per-user Startup folder:
REM   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
REM
REM Per-user Startup is used rather than a scheduled task because `schtasks /Create` needs
REM elevation and this does not.
REM
REM NOTE: a trycloudflare quick tunnel gets a NEW random hostname every start, so after a
REM reboot the public URL changes and Vercel's INFERENCE_URL has to be re-pointed at it.
REM The supervisor writes the current one to build\tunnel-url.txt. Switching to a named
REM Cloudflare tunnel removes that step entirely - see docs\serving-v18-free.md.

setlocal
set "ROOT=%~dp0.."
set "PYW=%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"

cd /d "%ROOT%"
if not exist "build" mkdir "build"

REM pythonw so logon does not leave a console window on screen.
REM
REM No redirection here on purpose: `start` returns immediately, so `>>` would only capture
REM start's own (empty) output and the detached process would write to nothing. The supervisor
REM appends to build\supervisor.log itself, which is where to look if nothing comes up.
start "" "%PYW%" "%ROOT%\scripts\serve-v18.py"
endlocal
