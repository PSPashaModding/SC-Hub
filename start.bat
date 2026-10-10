@echo off
chcp 65001 >nul
title SC Hub
python "%~dp0sc_hub.py" %*
if errorlevel 1 pause
