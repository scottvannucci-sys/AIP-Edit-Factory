@echo off
title Edit Factory
cd /d "%~dp0"

rem ---- find a python ----
set PY=
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
if not defined PY (
  for /f "delims=" %%i in ('dir /b /s /o:n "C:\Users\scott\.dsh\dsh-runtimes\*\dependencies\python\python.exe" 2^>nul') do (
    set PY=%%i
    goto :found
  )
)
:found
if not defined PY (
  where py >nul 2>nul && set PY=py
)
if not defined PY (
  where python >nul 2>nul && set PY=python
)
if not defined PY (
  echo Python not found. Install Python 3.10+ from python.org and run again.
  pause
  exit /b 1
)

echo ============================================
echo   EDIT FACTORY - Amazon Influencer pipeline
echo ============================================
echo.
echo Using: %PY%
echo.
"%PY%" edit_factory.py process %*
echo.
pause
