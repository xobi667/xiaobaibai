@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

cd /d "%~dp0"

echo Starting xobi Backend...
echo.

if not exist instance mkdir instance
if not exist uploads mkdir uploads

:: 检查并安装依赖
if exist requirements.txt (
    echo Checking dependencies...
    where py >nul 2>&1
    if %errorlevel% equ 0 (
        py -m pip install -r requirements.txt -q
    ) else (
        python -m pip install -r requirements.txt -q
    )
    echo.
)

echo Starting Flask server on port 5000...
echo.

echo Running database migrations...
echo.

:: 尝试不同的 Python 命令
where py >nul 2>&1
if %errorlevel% equ 0 (
    py -m alembic upgrade head
    py app.py
) else (
    python -m alembic upgrade head
    python app.py
)

echo.
echo Flask exited with code: %errorlevel%
echo.
pause
endlocal
