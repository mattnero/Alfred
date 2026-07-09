@echo off
cd /d %~dp0
.venv\Scripts\activate.bat
python brain_server.py --model qwen2.5:7b --port 8200
pause
