@echo off
REM Instala o Arena Replay (Windows). Requer Python 3.10+ no PATH.
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv || python -m venv .venv || goto :erro
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt || goto :erro
if not exist config.json copy config.example.json config.json
echo.
echo Instalacao concluida. Edite config.json (device_token) e rode run.bat
pause
exit /b 0
:erro
echo.
echo ERRO na instalacao. Confira se o Python 3.10+ esta instalado (python.org, marque "Add to PATH").
pause
exit /b 1
