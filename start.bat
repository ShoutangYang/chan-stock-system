@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动美股缠论交易系统...
python start.py
pause
