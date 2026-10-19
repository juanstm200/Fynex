@echo off
REM Alternativa al .exe de un solo archivo: carpeta dist\GiragMantenimiento\ con todo dentro.
REM Úsala si alguna política o herramienta limpia %TEMP% (el .exe de un solo archivo corre desde allí).
python -m PyInstaller --noconfirm --onedir --windowed --uac-admin ^
  --name GiragMantenimiento ^
  --add-data "config;config" ^
  main.py
echo.
echo Listo: dist\GiragMantenimiento\GiragMantenimiento.exe (copia la carpeta completa)
pause
