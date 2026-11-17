@echo off
setlocal
chcp 65001 >nul
set "INSTALLER=%~dp0installer.py"

where py >nul 2>nul
if errorlevel 1 goto try_python
py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
if errorlevel 1 goto try_python
py -3.12 "%INSTALLER%" %*
set "RESULT=%errorlevel%"
goto finish

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
if errorlevel 1 goto no_python
python "%INSTALLER%" %*
set "RESULT=%errorlevel%"
goto finish

:no_python
echo 未找到Python 3.12。请先安装Python 3.12，再重新双击本文件。
set "RESULT=2"

:finish
if "%RESULT%"=="0" goto success
echo 接入未完成，请根据上方提示处理后重试。
pause
exit /b %RESULT%

:success
echo 安装步骤完成，请按上方提示检查是否需要重新登录Windows。
pause
exit /b 0
