@echo off
title HefShine Downloader
cd /d "%~dp0"
cls
echo ========================================================
echo               HefShine Recordings Portal
echo ========================================================
echo.
echo Opening browser: http://localhost:8899
echo.
start "" "http://localhost:8899"
python server.py
pause
