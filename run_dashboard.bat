@echo off
REM Levanta el dashboard local en http://localhost:8501

cd /d C:\ai-trader
set PYTHONIOENCODING=utf-8
"C:\ai-trader\.venv\Scripts\streamlit.exe" run dashboard.py
