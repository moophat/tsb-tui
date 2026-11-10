@echo off
setlocal
set "TSB_TUI_HOME=%~dp0"
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH=%~dp0lib\site"
set "TSB_ENGINE="
set /p "TSB_ENGINE=" < "%~dp0lib\engine\ENGINE"
if not defined TSB_ENGINE exit /b 1
set "TSB_TUI_TERM_LIBRARY=%~dp0lib\engine\%TSB_ENGINE%"
set "PATH=%~dp0lib\engine;%PATH%"
"%~dp0python\python.exe" -m tsb_tui %*
exit /b %errorlevel%
