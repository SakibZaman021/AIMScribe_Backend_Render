@echo off
echo ============================================================
echo        AIMScribe Backend - Stop Services (Docker)
echo ============================================================
echo.

docker-compose down

echo.
echo All services stopped.
pause
