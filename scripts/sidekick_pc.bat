@echo off
setlocal
cd /d "%~dp0.."
python app\sidekick_pc.py
if errorlevel 1 pause