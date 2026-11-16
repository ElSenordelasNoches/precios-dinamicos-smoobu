@echo off
rem Hace que el panel y el tunel arranquen solos al iniciar sesion en Windows.
rem Ejecutelo una vez con doble clic (fuera de la aplicacion de Claude).
setlocal
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PY%" for /f "delims=" %%i in ('where pythonw 2^>nul') do set "PY=%%i"
if not exist "%PY%" (
  echo No se encuentra Python. Instale Python 3.10 o superior y vuelva a intentarlo.
  pause
  exit /b 1
)
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v PreciosDinamicos /t REG_SZ /d "\"%PY%\" \"%~dp0servicio.py\"" /f >nul
if errorlevel 1 (echo No se ha podido registrar el arranque automatico.) else (echo Arranque automatico registrado.)
start "" "%PY%" "%~dp0servicio.py"
echo Servicio arrancado. Ya puede cerrar esta ventana.
pause
