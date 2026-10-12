@echo off
cd /d %~dp0
rem Use the venv's python directly — running activate.bat without CALL never returns.
.venv\Scripts\python.exe brain_server.py --model qwen2.5:7b --port 8200
pause
