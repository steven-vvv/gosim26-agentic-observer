@echo off
REM Windows: double-click to run the baseline agent on the finals-preview scenario (anomaly mechanics on) and open the replay.
REM Seven nights with the finals mechanics on: hidden tile tags, an instrument fault, score feedback and the report channel.
cd /d "%~dp0"
set PY=
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY ( python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=python" )
if not defined PY (
  echo No Python 3.9+ found. Install Python 3.12 from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
%PY% --version
%PY% local_runner.py --scenario scenarios\finals-preview --agent agent\minimal_agent.py --wallclock 900 --out demo_week_output
set STATUS=%ERRORLEVEL%
if exist demo_week_output\decision_replay.html start "" demo_week_output\decision_replay.html
echo.
echo Done (exit %STATUS%). Score report: demo_week_output\score_report.json
pause
