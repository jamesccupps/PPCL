@echo off
rem ---------------------------------------------------------------------------
rem PPCL Workbench launcher.
rem
rem   Double-click            opens the workbench in a browser
rem   ppcl.bat lint progs\    runs any subcommand instead
rem   ppcl.bat --help         lists them
rem
rem Nothing to install: the toolkit is stdlib-only and needs Python 3.10+.
rem ---------------------------------------------------------------------------

setlocal
cd /d "%~dp0"

rem The Windows launcher first, because it finds a real install in preference
rem to the Microsoft Store build, whose file-write sandbox breaks Save.
set "PPCL_PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PPCL_PY=py -3"
if defined PPCL_PY goto :run

python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PPCL_PY=python"
if defined PPCL_PY goto :run

python3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PPCL_PY=python3"
if defined PPCL_PY goto :run

echo.
echo   No Python 3.10 or newer was found on this machine.
echo.
echo   Install it from https://www.python.org/downloads/ and run this again.
echo   The Microsoft Store build works for the command line but sandboxes
echo   file writes, which breaks Save in the editor.
echo.
pause
exit /b 1

:run
rem ppcl.cli rather than ppcl, so this also works against an older copy that
rem predates ppcl/__main__.py.
if "%~1"=="" goto :serve

rem Called with a subcommand: behave like any other command-line tool and
rem pass the exit code straight through. lint exits 1 when it finds
rem something, which is not a failure and must not stop to be acknowledged.
%PPCL_PY% -m ppcl.cli %*
exit /b %errorlevel%

:serve
echo.
echo   Starting the PPCL Workbench. It will open in your browser.
echo   Close this window to stop it.
echo.
%PPCL_PY% -m ppcl.cli serve
if errorlevel 1 goto :held
exit /b 0

:held
rem Only reached when the server itself failed to start. A double-clicked
rem window closes before the reason can be read, so hold it open.
echo.
echo   The workbench did not start. The reason is above.
echo.
pause
exit /b 1
