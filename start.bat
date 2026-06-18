@echo off
title Geppetto 3 — Meeting Truth Layer

echo.
echo  Starting Geppetto 3...
echo.

REM Start server (audio streamer launches automatically when you click "Start live meeting")
start "Geppetto 3 — Server" cmd /k "cd /d %~dp0 && py -3.12 -m uvicorn phase3_server_realtime:app --host 127.0.0.1 --port 8000"

REM Wait for server to be ready
timeout /t 4 /nobreak >nul

REM Open dashboard
start http://127.0.0.1:8000

echo  Server started. Dashboard opening in browser.
echo  Click "Start live meeting" to begin — audio capture starts automatically.
echo.
pause
