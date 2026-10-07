@echo off
chcp 65001 >nul
cd /d "%~dp0"
python client_cli.py --name "Tuấn" --tone "tuan"
if errorlevel 1 pause
