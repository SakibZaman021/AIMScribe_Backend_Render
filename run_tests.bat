@echo off
echo ============================================================
echo        AIMScribe Backend - API Test Runner
echo ============================================================
echo.

REM Activate virtual environment if exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Run tests
python tests/test_azure_apis.py

echo.
pause
