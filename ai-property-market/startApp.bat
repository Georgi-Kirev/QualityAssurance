@echo off
REM ================================================================
REM   AI PROPERTY MARKET - ONE-CLICK DASHBOARD LAUNCHER (Windows)
REM
REM   Sets up the sample data, runs the local pipeline and starts
REM   the Flask dashboard. No network access is required or made.
REM
REM   Safe ASCII encoding. Requires Windows 10/11, Python 3.10+.
REM ================================================================

setlocal EnableExtensions DisableDelayedExpansion

title AI Property Market - Dashboard
cd /d "%~dp0"
set "BASEDIR=%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set "PY_EXE="
set "BROWSER_URL=http://127.0.0.1:5000/"
set "HEALTH_URL=http://127.0.0.1:5000/health"
set FLASK_HOST=127.0.0.1
set FLASK_PORT=5000

echo.
echo ================================================================
echo    AI Property Market  -  starting up, please wait ...
echo ================================================================
echo.

REM ---------- 1. Locate python interpreter ----------
if exist "%BASEDIR%.venv\Scripts\python.exe" (
    echo [OK] Found virtual env python: .venv\Scripts\python.exe
    set "PY_EXE=%BASEDIR%.venv\Scripts\python.exe"
    set "PY_EXTRA="
    goto :python_found
)

where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [OK] Using py launcher for Python 3
    set "PY_EXE=py"
    set "PY_EXTRA=-3"
    goto :python_found
)

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [OK] Using system python
    set "PY_EXE=python"
    set "PY_EXTRA="
    goto :python_found
)

echo.
echo [ERROR] Python interpreter not found on this machine.
echo         Please install Python 3.10+ from https://www.python.org/downloads/
echo         IMPORTANT: during install CHECK the option "Add python.exe to PATH".
echo.
echo Press any key to exit ...
pause >nul
exit /b 1

:python_found
echo.

REM ---------- 2. Ensure required packages ----------
echo [INFO] Checking required Python packages (one-time install if missing) ...
"%PY_EXE%" %PY_EXTRA% -c "import flask, requests, ijson, tzdata, psutil" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing: tzdata, flask, requests, ijson, psutil
    echo [INFO] This may take 30-90 seconds ...
    "%PY_EXE%" %PY_EXTRA% -m pip install --disable-pip-version-check tzdata flask requests ijson psutil
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install required pip packages.
        echo Press any key to exit ...
        pause >nul
        exit /b 2
    )
) else (
    echo [OK] All Python packages are installed.
)
echo.

REM ---------- 3. Prepare sample data and run the local pipeline ----------
echo [INFO] Preparing sample data ...
"%PY_EXE%" "%BASEDIR%main.py" --setup
if errorlevel 1 goto :failed

echo.
echo [INFO] Running local pipeline (normalize - dedup - consolidate - export) ...
"%PY_EXE%" "%BASEDIR%main.py" --pipeline
if errorlevel 1 goto :failed

echo.

REM ---------- 4. Open browser after short delay ----------
echo [INFO] Starting Flask dashboard. Browser will open automatically at %BROWSER_URL%
echo.
start "" /min cmd /c "timeout /t 4 /nobreak >nul && start "" "%BROWSER_URL%""

REM ---------- 5. Instructions banner ----------
echo ================================================================
echo   DASHBOARD URL   :  %BROWSER_URL%
echo   HEALTH CHECK    :  %HEALTH_URL%
echo   OFFLINE MODE    :  downloads are disabled in this build
echo   SAMPLE DATA     :  sample_data\ (bundled, no network needed)
echo   TO STOP         :  press Ctrl+C or close this window.
echo ================================================================
echo.

REM ---------- 6. Launch the application ----------
"%PY_EXE%" %PY_EXTRA% "%BASEDIR%main.py" --api --host %FLASK_HOST% --port %FLASK_PORT%

echo.
echo [INFO] Application stopped. Press any key to close this window ...
pause >nul
exit /b 0

:failed
echo.
echo [ERROR] Setup or pipeline failed. Scroll up for the reason.
echo Press any key to exit ...
pause >nul
exit /b 1