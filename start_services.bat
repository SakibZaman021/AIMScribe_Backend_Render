@echo off
echo ============================================================
echo        AIMScribe Backend - Start Services (Docker)
echo ============================================================
echo.

REM Check Docker
docker --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker is not installed or not in PATH
    echo Please install Docker Desktop from https://www.docker.com/products/docker-desktop
    pause
    exit /b 1
)

echo Starting PostgreSQL, Redis, and MinIO...
echo.

docker-compose up -d

echo.
echo ============================================================
echo        Services Started!
echo ============================================================
echo.
echo Service Status:
docker-compose ps
echo.
echo Access Points:
echo   PostgreSQL: localhost:5432
echo   Redis:      localhost:6379
echo   MinIO API:  localhost:9000
echo   MinIO Console: http://localhost:9001 (user: aimscribe / pass: aimscribe123)
echo.
echo To view logs: docker-compose logs -f
echo To stop:      docker-compose down
echo.
pause
