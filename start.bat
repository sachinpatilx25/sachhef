@echo off
title HefShine Recordings Manager
cd /d "%~dp0"

echo ========================================================
echo         HefShine Lecture Recordings Manager
echo ========================================================
echo.

REM Check if requests is installed
python -c "import requests" 2>nul
if %errorlevel% neq 0 (
    echo Installing required requests library...
    pip install requests
)

REM Check if ffmpeg exists in app directory, if not download automatically
if not exist "%~dp0ffmpeg.exe" (
    echo Setting up FFmpeg video merger...
    python -c "import imageio_ffmpeg, shutil, os; shutil.copyfile(imageio_ffmpeg.get_ffmpeg_exe(), 'ffmpeg.exe')" 2>nul
    if not exist "%~dp0ffmpeg.exe" (
        pip install imageio-ffmpeg
        python -c "import imageio_ffmpeg, shutil, os; shutil.copyfile(imageio_ffmpeg.get_ffmpeg_exe(), 'ffmpeg.exe')"
    )
)

echo Starting portal...
echo Opening in browser: http://localhost:8899
echo.

start "" "http://localhost:8899"
python server.py

pause
