@echo off
REM Genera dist\GiragMantenimiento.exe (requiere: pip install pyinstaller)
python -m PyInstaller --noconfirm --onefile --windowed --uac-admin ^
  --name GiragMantenimiento ^
  --add-data "config;config" ^
  main.py
echo.
echo Listo: dist\GiragMantenimiento.exe
pause
