@echo off
echo ============================================================
echo        AIMScribe Backend - Worker
echo ============================================================
echo.

REM Activate virtual environment if exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

echo Starting Worker (listening for jobs)...
echo Press Ctrl+C to stop
echo.

python src/worker.py
