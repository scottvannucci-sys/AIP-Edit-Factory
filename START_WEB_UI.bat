@echo off
title Edit Factory - Web UI
cd /d "%~dp0"

rem ---- find a python ----
rem NOTE: prefer real pythons over .venv. A venv inside a OneDrive/Dropbox
rem sync folder breaks child-process spawning (0xc0000142 / Access denied).
set PY=
where py >nul 2>nul && set PY=py
if not defined PY (
  where python >nul 2>nul && set PY=python
)
if not defined PY (
  for /f "delims=" %%i in ('dir /b /s /o:n "C:\Users\scott\.dsh\dsh-runtimes\*\dependencies\python\python.exe" 2^>nul') do (
    set PY=%%i
    goto :found
  )
)
:found
if not defined PY (
  if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
)
if not defined PY (
  echo Python not found. Install Python 3.10+ from python.org and run again.
  pause
  exit /b 1
)

echo ============================================
echo   EDIT FACTORY WEB UI
echo   http://127.0.0.1:8765
echo ============================================
echo.
echo Using: %PY%
echo (leave this window open while editing; close it to stop)
echo.
"%PY%" webui.py
echo.
echo Web UI stopped.
pause
