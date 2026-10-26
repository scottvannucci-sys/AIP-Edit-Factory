@echo off
title Edit Factory - Web UI
cd /d "%~dp0"

rem ---- find a working python (validates each candidate) ----
rem The Microsoft Store "python" alias prints an ad and exits nonzero,
rem so candidates are test-run before being accepted.
set PY=
call :findpython
if not defined PY (
  echo.
  echo Python was not found on this PC.
  echo Double-click SETUP.bat first - it installs everything automatically.
  echo.
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
goto :eof

:findpython
rem 1) py launcher
for /f "delims=" %%i in ('where py 2^>nul') do if not defined PY call :validate "%%i"
rem 2) python on PATH (rejects the Store alias stub automatically)
for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY call :validate "%%i"
rem 3) common install locations (covers PATH not refreshed after winget/setup)
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "C:\Program Files\Python313\python.exe"
  "C:\Program Files\Python312\python.exe"
  "C:\Program Files\Python311\python.exe"
) do if not defined PY if exist %%P call :validate %%P
rem 4) this PC's bundled runtime
for /d %%D in ("C:\Users\scott\.dsh\dsh-runtimes\*") do if not defined PY if exist "%%~fD\dependencies\python\python.exe" call :validate "%%~fD\dependencies\python\python.exe"
rem 5) local venv last (avoid venvs in sync folders when possible)
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" call :validate "%~dp0.venv\Scripts\python.exe"
goto :eof

:validate
"%~1" -c "print(1)" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
goto :eof
