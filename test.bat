@echo off
REM Roda os testes automatizados. Use: test.bat   ou   test.bat -m "not slow"
cd /d "%~dp0"
call .venv\Scripts\activate.bat || (echo Rode setup.bat primeiro & pause & exit /b 1)
python -m pytest -v %*
pause
