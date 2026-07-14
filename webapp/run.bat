@echo off
cd /d "%~dp0.."
set FOODCAL_DIR=%CD%

echo FoodCal Web App
echo   Demo mode (no GPU):  set FOODCAL_DEMO=1
echo   GPU pipeline:        leave FOODCAL_DEMO unset
echo.
echo Open http://127.0.0.1:8000
echo.

py -m pip install -q -r webapp\requirements.txt
if not defined FOODCAL_DEMO set FOODCAL_DEMO=1
py -m uvicorn webapp.server:app --host 0.0.0.0 --port 8000 --reload
