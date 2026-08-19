@echo off
rem Watches Relay and brings it back when you ask from Telegram.
rem Leave it running; it costs a wake-up every few seconds.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m relay.keeper
