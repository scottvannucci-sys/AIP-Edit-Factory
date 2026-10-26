@echo off
title Edit Factory
cd /d "%~dp0"

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
echo   EDIT FACTORY - Amazon Influencer pipeline
echo ============================================
echo.
echo Using: %PY%
echo.
"%PY%" edit_factory.py process %*
echo.
pause
goto :eof

:findpython
for /f "delims=" %%i in ('where py 2^>nul') do if not defined PY call :validate "%%i"
for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY call :validate "%%i"
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "C:\Program Files\Python313\python.exe"
  "C:\Program Files\Python312\python.exe"
  "C:\Program Files\Python311\python.exe"
) do if not defined PY if exist %%P call :validate %%P
for /d %%D in ("C:\Users\scott\.dsh\dsh-runtimes\*") do if not defined PY if exist "%%~fD\dependencies\python\python.exe" call :validate "%%~fD\dependencies\python\python.exe"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" call :validate "%~dp0.venv\Scripts\python.exe"
goto :eof

:validate
"%~1" -c "print(1)" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
goto :eof
