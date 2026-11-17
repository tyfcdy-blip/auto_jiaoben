@echo off
setlocal
chcp 65001 >nul
call "%~dp0安装并连接.cmd" --upgrade-existing %*
exit /b %errorlevel%
