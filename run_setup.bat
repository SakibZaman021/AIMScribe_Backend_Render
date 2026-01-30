@echo off
echo ============================================================
echo        AIMScribe Backend - Setup Wizard
echo ============================================================
echo.

REM Activate virtual environment if exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Run setup
python scripts/setup.py

echo.
pause
