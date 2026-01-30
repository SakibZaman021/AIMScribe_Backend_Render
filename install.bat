@echo off
echo ============================================================
echo        AIMScribe Backend - Installation
echo ============================================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Python found:
python --version
echo.

REM Create virtual environment
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
    echo Virtual environment created.
) else (
    echo Virtual environment already exists.
)
echo.

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Upgrade pip
echo Upgrading pip...
python -m pip install --upgrade pip
echo.

REM Install dependencies
echo Installing dependencies from requirements.txt...
pip install -r requirements.txt
echo.

REM Install additional required packages
echo Installing additional packages...
pip install python-dotenv
echo.

echo ============================================================
echo        Installation Complete!
echo ============================================================
echo.
echo Next steps:
echo   1. Edit .env file with your Azure credentials
echo   2. Run: run_setup.bat    (Initialize database)
echo   3. Run: run_tests.bat    (Test API connections)
echo   4. Run: run_server.bat   (Start API server)
echo   5. Run: run_worker.bat   (Start worker - in new terminal)
echo.

pause
