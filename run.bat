@echo off
REM run.bat - TINA's entry point. Sets up venv\ the first time, then starts TINA
REM on http://127.0.0.1:5013/. Her long-term memory is the Hindsight server DREAM
REM hosts (DREAM's run.bat starts it); HindsightUrl in config.json points at it.
REM NOTE(move): TINA is meant to move to the other PC later; DREAM keeps the memory.
setlocal
cd /d "%~dp0"

ollama list | findstr /b "qwen3:4b" >/dev/null || ollama pull qwen3:4b

if not exist "venv\Scripts\python.exe" (py -3 -m venv venv || python -m venv venv)
fc /b requirements.txt "venv\requirements.installed" >/dev/null 2>&1
if errorlevel 1 (
    venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
    copy /y requirements.txt "venv\requirements.installed" >nul
)

venv\Scripts\python.exe tina.py %*
if errorlevel 1 pause
exit /b

:fail
echo Setup failed - see the errors above.
pause
exit /b 1
