@echo off
setlocal
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH=%~dp0lib\site"
set "TSB_ENGINE="
set /p "TSB_ENGINE=" < "%~dp0lib\engine\ENGINE"
if not defined TSB_ENGINE exit /b 1
set "TSB_TUI_TERM_LIBRARY=%~dp0lib\engine\%TSB_ENGINE%"
set "PATH=%~dp0lib\engine;%PATH%"
"%~dp0python\python.exe" -c "from tsb_tui.app import load_engine; from tsb_tui.config import load; from importlib.metadata import version; engine = load_engine(load()); print('Engine:', engine.build_info.version_string); print('Manifest check: PASS'); print('TUI:', version('tsb-tui'))"
exit /b %errorlevel%
