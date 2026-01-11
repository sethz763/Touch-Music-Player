# build.cmd - Build the Step D Audio Player with PyInstaller
@echo off
cd /d "%~dp0"

REM Activate the virtual environment
call ..\venv\Scripts\activate.bat

REM Force clean previous builds manually (in case PyInstaller --clean fails)
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM Run PyInstaller with the spec file
pyinstaller music_player.spec

REM Deactivate venv
call deactivate

echo Build complete. Check the dist/ folder for the executable.
pause