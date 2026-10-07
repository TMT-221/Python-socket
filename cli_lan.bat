@echo off
chcp 65001 >nul
cd /d "%~dp0"
python client_cli.py --name "Lan" --tone "lan"
if errorlevel 1 pause
