@echo off
echo ============================================================
echo        AIMScribe Backend - API Server
echo ============================================================
echo.

REM Activate virtual environment if exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

echo Starting FastAPI Server on port 6000...
echo Press Ctrl+C to stop
echo.

python src/main_fastapi.py
