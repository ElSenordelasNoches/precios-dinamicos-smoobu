@echo off
rem Abre el panel de precios dinamicos en el navegador.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 abrir_panel.py & goto :fin)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" abrir_panel.py & goto :fin)
python abrir_panel.py
:fin
pause
