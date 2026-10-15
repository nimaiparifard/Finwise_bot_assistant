@echo off
REM ==========================================================================
REM  FinWise Course Bot - one-click start
REM  Safe to run every time: each step only does work when it is needed.
REM ==========================================================================
setlocal
cd /d "%~dp0"
title FinWise Course Bot - setup

set "VENV_PY=.venv\Scripts\python.exe"
set "MONGO_CONTAINER=finwise-mongo"
set "ADMIN_PORT=8000"

echo.
echo [1/8] Checking requirements...
where python >nul 2>&1
if errorlevel 1 (
    echo   ERROR: Python was not found. Install Python 3.12+ from https://www.python.org/ and tick "Add to PATH".
    goto :fail
)
docker info >nul 2>&1
if errorlevel 1 (
    echo   ERROR: Docker is not running. Start Docker Desktop, wait until it is ready, then run this file again.
    goto :fail
)
echo   OK

echo.
echo [2/8] Starting MongoDB...
REM Reuse the existing container if there is one (keeps its data); otherwise use docker compose.
docker container inspect %MONGO_CONTAINER% >nul 2>&1
if errorlevel 1 (
    docker compose up -d
) else (
    docker start %MONGO_CONTAINER% >nul
)
if errorlevel 1 (
    echo   ERROR: Could not start MongoDB. Is another program using port 27017?
    goto :fail
)
echo   OK

echo.
echo [3/8] Preparing Python virtual environment...
if not exist "%VENV_PY%" (
    python -m venv .venv
    if errorlevel 1 goto :fail
)
"%VENV_PY%" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo   ERROR: Installing requirements failed.
    goto :fail
)
echo   OK

echo.
echo [4/8] Checking .env configuration...
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo   Created .env from .env.example.
    echo   Fill in TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, OPENAI_BASE_URL and OPENAI_MODEL,
    echo   then SAVE and CLOSE Notepad to continue.
    notepad ".env"
)
echo   OK

echo.
echo [5/8] Waiting for MongoDB to accept connections...
set /a TRIES=0
:wait_mongo
"%VENV_PY%" -c "import os,pymongo;from dotenv import load_dotenv;load_dotenv();pymongo.MongoClient(os.getenv('MONGODB_URI','mongodb://localhost:27017/'),serverSelectionTimeoutMS=2000).admin.command('ping')" >nul 2>&1
if not errorlevel 1 goto :mongo_ready
set /a TRIES+=1
if %TRIES% GEQ 15 (
    echo   ERROR: MongoDB did not respond. Check MONGODB_URI in .env and "docker ps".
    goto :fail
)
timeout /t 2 /nobreak >nul
goto :wait_mongo
:mongo_ready
echo   OK

echo.
echo [6/8] Applying database migrations...
"%VENV_PY%" manage.py migrate --noinput
if errorlevel 1 goto :fail

echo.
echo [7/8] Checking admin user and course data...
"%VENV_PY%" manage.py shell -v 0 -c "import sys;from django.contrib.auth.models import User;sys.exit(0 if User.objects.filter(is_superuser=True).exists() else 1)" >nul 2>&1
if errorlevel 1 (
    echo   No admin user yet - create one for the admin panel:
    "%VENV_PY%" manage.py createsuperuser
    if errorlevel 1 goto :fail
) else (
    echo   Admin user exists.
)
"%VENV_PY%" manage.py shell -v 0 -c "import sys;from courses.models import Course;sys.exit(0 if Course.objects.exists() else 1)" >nul 2>&1
if errorlevel 1 (
    echo   No courses yet - crawling finwisecenter.com, this takes about a minute...
    "%VENV_PY%" manage.py crawl_courses
    if errorlevel 1 echo   WARNING: Crawl failed. Retry later with the "Crawl website now" button in the admin panel.
) else (
    echo   Course data exists. Use "Crawl website now" in the admin panel to refresh it.
)

echo.
echo [8/8] Starting admin panel and Telegram bot in new windows...
start "FinWise - Admin panel" cmd /k ""%VENV_PY%" manage.py runserver %ADMIN_PORT%"
start "FinWise - Telegram bot" cmd /k ""%VENV_PY%" manage.py runbot"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:%ADMIN_PORT%/admin/"

echo.
echo ==========================================================================
echo  Running!
echo   Admin panel : http://127.0.0.1:%ADMIN_PORT%/admin/
echo   Telegram bot: see the "FinWise - Telegram bot" window
echo  To stop: close those two windows. MongoDB keeps running in Docker;
echo  stop it with:  docker stop %MONGO_CONTAINER%   (or: docker compose down)
echo ==========================================================================
echo.
pause
exit /b 0

:fail
echo.
echo Setup stopped because of the error above.
pause
exit /b 1
