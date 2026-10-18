@echo off
REM Inicia o Arena Replay. Use: run.bat   ou   run.bat --synthetic (sem webcam)
cd /d "%~dp0"
call .venv\Scripts\activate.bat || (echo Rode setup.bat primeiro & pause & exit /b 1)
python -m app.main %*
pause
