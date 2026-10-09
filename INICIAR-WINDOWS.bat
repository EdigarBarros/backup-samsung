@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (py -3 backup_android.py %*) || (python backup_android.py %*)
pause
