@echo off
title Edit Factory - Setup
cd /d "%~dp0"
setlocal

echo ============================================
echo   EDIT FACTORY SETUP
echo   Installs Python, FFmpeg and packages.
echo ============================================
echo.

set PY=
call :findpython

rem ---- 1. python ----
if not defined PY (
  echo [1/3] Python not found - installing via winget ^(a few minutes^)...
  winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo.
    echo   winget install failed. Install Python manually:
    echo   https://www.python.org/downloads/
    echo   IMPORTANT: check "Add python.exe to PATH" during install.
    pause
    exit /b 1
  )
  call :findpython
  if not defined PY (
    echo.
    echo   Python was installed but is not visible yet.
    echo   Close this window, open a new one, and run SETUP.bat again.
    pause
    exit /b 1
  )
)
echo [1/3] Python OK: %PY%

rem ---- 2. ffmpeg ----
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [2/3] FFmpeg not found - installing via winget...
  winget install --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
  where ffmpeg >nul 2>nul
  if errorlevel 1 (
    echo   FFmpeg is not on PATH in this window yet.
    echo   If processing later complains about ffmpeg, close and reopen
    echo   this window ^(or reboot^) so PATH refreshes.
  ) else (
    echo [2/3] FFmpeg OK
  )
) else (
  echo [2/3] FFmpeg OK
)

rem ---- 3. python packages ----
echo [3/3] Installing Python packages ^(one-time, a few minutes^)...
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo   pip install failed - check your internet connection and run SETUP.bat again.
  pause
  exit /b 1
)
"%PY%" -c "import faster_whisper, fastapi, uvicorn"
if errorlevel 1 (
  echo   Validation failed - run SETUP.bat again.
  pause
  exit /b 1
)

echo.
echo ============================================
echo   SETUP COMPLETE
echo   Double-click START_WEB_UI.bat to begin.
echo   (First run downloads the Whisper model, ~460 MB, one time.)
echo ============================================
pause
exit /b 0

rem ---- subroutines ----

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
