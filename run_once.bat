@echo off
REM Lanza un ciclo del bot en testnet. Usado por Task Scheduler.
REM El working directory se fija aquí para que los paths relativos funcionen.

cd /d C:\ai-trader
set PYTHONIOENCODING=utf-8
"C:\ai-trader\.venv\Scripts\python.exe" -m ai_trader once --mode testnet >> "C:\LogsTraiding\ai-trader\scheduler.log" 2>&1
