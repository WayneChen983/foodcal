@echo off
REM 將本機 webapp 同步到 RunPod（需先設定 RUNPOD_SSH）
REM 用法：set RUNPOD_SSH=root@xxx.runpod.io -p 12345
REM       webapp\sync_to_runpod.bat

if not defined RUNPOD_SSH (
  echo.
  echo 請先在 RunPod Pod 頁面 Connect -^> SSH 取得連線字串，例如：
  echo   set RUNPOD_SSH=root@1.2.3.4 -p 22000
  echo.
  set /p RUNPOD_SSH=貼上 SSH 連線 ^(user@host -p port^): 
)

if "%RUNPOD_SSH%"=="" exit /b 1

set REMOTE_DIR=/workspace/foodcal
echo 同步 webapp 到 RunPod %REMOTE_DIR%/webapp ...

scp -r "%~dp0server.py" "%~dp0requirements.txt" "%~dp0static" %RUNPOD_SSH%:%REMOTE_DIR%/webapp/
scp "%~dp0..\scripts\runpod_webapp.sh" %RUNPOD_SSH%:%REMOTE_DIR%/scripts/

echo.
echo 完成。在 RunPod Terminal 執行：
echo   bash scripts/runpod_webapp.sh
echo.
