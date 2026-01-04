@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul

set "ROOT=%~dp0"
pushd "%ROOT%" >nul

set "BACKEND_DIR=%ROOT%backend"
set "FRONTEND_DIR=%ROOT%frontend"
set "FRONTEND_DIST_INDEX=%FRONTEND_DIR%\dist\index.html"
set "BACKEND_PORT=5000"
if exist "%ROOT%.env" (
  for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /i "PORT=" "%ROOT%.env"`) do set "BACKEND_PORT=%%B"
)

echo ========================================
echo   xobi - YunWu API Edition
echo ========================================
echo.
echo Tips:
echo 1. This version uses YunWu.ai third-party API
echo 2. After startup, go to Settings to enter your API Key
echo.

:: Check required files
if not exist "%BACKEND_DIR%\run.bat" ( echo [Error] backend\run.bat not found && pause && goto END )
if not exist "%FRONTEND_DIR%\start.bat" ( echo [Error] frontend\start.bat not found && pause && goto END )

:: Start backend (new window)
echo Starting backend service...
start "xobi-backend" /D "%BACKEND_DIR%" cmd /k run.bat

echo Waiting for backend to be ready (http://127.0.0.1:%BACKEND_PORT%/health)...
set "BACKEND_RETRIES=60"
:WAIT_BACKEND
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 'http://127.0.0.1:%BACKEND_PORT%/health'; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %errorlevel% equ 0 goto BACKEND_READY
set /a BACKEND_RETRIES-=1
if %BACKEND_RETRIES% leq 0 goto BACKEND_TIMEOUT
timeout /t 1 /nobreak >nul
goto WAIT_BACKEND
:BACKEND_READY
echo Backend is ready.
goto AFTER_BACKEND
:BACKEND_TIMEOUT
echo [Warning] Backend is not ready yet, starting frontend anyway...
goto AFTER_BACKEND

rem Prefer serving built frontend from backend when dist exists.
:AFTER_BACKEND
if not exist "%FRONTEND_DIST_INDEX%" (
  echo frontend\dist not found, building UI...
  pushd "%FRONTEND_DIR%" >nul
  if not exist "node_modules\" (
    echo Installing frontend dependencies...
    call npm install
  )
  echo Building frontend...
  call npm run build
  popd >nul
)

echo Using built-in UI served by backend.
echo Opening: http://127.0.0.1:%BACKEND_PORT%
start "" "http://127.0.0.1:%BACKEND_PORT%/"
goto DONE

:DONE
echo.
echo All services started.
echo If browser did not open, manually visit: http://127.0.0.1:%BACKEND_PORT%
echo.
pause
:END
popd >nul
endlocal
