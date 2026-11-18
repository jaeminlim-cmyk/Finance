@echo off
rem AI hedge fund (paper trading) - one-click launcher for Windows.
rem Needs only Python 3.11 or newer. Messages are in English on purpose:
rem Korean text inside .bat files breaks on some Windows consoles.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1

rem Packages go to a short path outside the project. Inside a long folder name
rem (for example under Downloads) Windows' 260-character path limit breaks pip.
set "VENV=%LOCALAPPDATA%\aifund\venv"
set "VPY=%VENV%\Scripts\python.exe"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python313\python.exe""
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python311\python.exe""
if not defined PY set "PY=python"

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto nopython

if exist "%VPY%" goto havevenv
echo [1/4] Creating the Python environment in %VENV%
%PY% -m venv "%VENV%"
if errorlevel 1 goto failed
:havevenv

fc /b "backend\requirements.txt" "%VENV%\requirements.txt" >nul 2>nul
if not errorlevel 1 goto installed
echo [2/4] Installing packages. This takes a few minutes the first time...
"%VPY%" -m pip install --disable-pip-version-check -q -r "backend\requirements.txt"
if errorlevel 1 goto failed
copy /y "backend\requirements.txt" "%VENV%\requirements.txt" >nul
:installed

if not exist "backend\app\static\index.html" goto nostatic

if exist "data\fund.db" goto havedata
echo [3/4] Replaying the last 60 trading days with sample data...
pushd backend
"%VPY%" -m app.cli simulate --days 60 >nul
if errorlevel 1 (popd & goto failed)
popd
:havedata

echo [4/4] Starting the dashboard at http://127.0.0.1:8000
echo       Keep this window open. Press Ctrl+C or close it to stop.
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://127.0.0.1:8000"
pushd backend
"%VPY%" -m uvicorn app.api:app --host 127.0.0.1 --port 8000
popd
goto end

:nopython
echo.
echo [ERROR] Python 3.11 or newer was not found.
echo   1. Run this command:   winget install Python.Python.3.12
echo   2. Close this window.
echo   3. Double-click start.bat again.
goto end

:nostatic
echo.
echo [ERROR] The dashboard files are missing: backend\app\static
echo   Download the project again, or build them:  cd frontend, npm install, npm run build
goto end

:failed
echo.
echo [ERROR] A step failed. Copy the messages above and send them to get help.

:end
echo.
pause
