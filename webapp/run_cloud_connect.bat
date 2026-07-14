@echo off
cd /d "%~dp0.."
set FOODCAL_DIR=%CD%

echo ============================================
echo  FoodCal 本機 UI + 雲端 GPU 推論
echo ============================================
echo.
echo 請先在 RunPod Pod 上執行：
echo   bash scripts/runpod_webapp.sh
echo.
echo 再到 RunPod 控制台 Connect -^> HTTP Port 8000
echo 複製 proxy URL，例如：
echo   https://xxxxxxxx-8000.proxy.runpod.net
echo.

if not defined FOODCAL_REMOTE_API (
  set /p FOODCAL_REMOTE_API=貼上 RunPod HTTP URL: 
)

if "%FOODCAL_REMOTE_API%"=="" (
  echo [ERROR] 未設定 FOODCAL_REMOTE_API
  pause
  exit /b 1
)

echo.
echo 雲端 API: %FOODCAL_REMOTE_API%
echo 本機 UI:  http://127.0.0.1:8000
echo.

py -m pip install -q -r webapp\requirements.txt
py -m uvicorn webapp.server:app --host 127.0.0.1 --port 8000
