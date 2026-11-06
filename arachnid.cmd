@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem Arachnid through Docker, for Windows.  Only Docker Desktop is needed - no Python, no npm.
rem   arachnid scan "C:\path\to\project"         look only, change nothing
rem   arachnid dashboard "C:\path\to\project"    then open http://127.0.0.1:8765
rem   arachnid fix "C:\path\to\project"          arachnid undo "C:\path\to\project"
rem   arachnid help                              arachnid update   (get the newest Arachnid)
rem Without a folder it uses the folder you are in.

if defined ARACHNID_IMAGE (set "IMAGE=%ARACHNID_IMAGE%") else (set "IMAGE=ghcr.io/meetraval09/arachnid:latest")
if not defined ARACHNID_PORT set "ARACHNID_PORT=8765"

where docker >nul 2>nul || goto :nodocker
docker info >nul 2>nul || goto :notrunning

set "CMD=%~1"
if "%CMD%"=="" set "CMD=help"
shift
if /i "%CMD%"=="update" goto :update
if /i "%CMD%"=="help" goto :noproject
if /i "%CMD%"=="version" goto :noproject
if /i "%CMD%"=="selftest" goto :noproject
if /i "%CMD%"=="accuracy" goto :noproject

set "PROJ=%CD%"
if "%~1"=="" goto :haveproj
if not exist "%~1\" goto :haveproj
set "PROJ=%~f1"
shift
:haveproj
if "%PROJ:~-1%"=="\" set "PROJ=%PROJ:~0,-1%"
for %%I in ("%PROJ%") do set "PNAME=%%~nxI"

set "REST="
:collect
if "%~1"=="" goto :run
set REST=%REST% "%~1"
shift
goto :collect

:run
rem One storage box (Docker volume) per project, so results and undo backups survive between runs
set "H="
for /f "usebackq delims=" %%h in (`powershell -NoProfile -Command "[BitConverter]::ToString([Security.Cryptography.SHA1]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($env:PROJ.ToLower()))).Replace('-','').Substring(0,12).ToLower()"`) do set "H=%%h"
if not defined H set "H=default"

set "PORTS="
if /i "%CMD%"=="dashboard" set "PORTS=-p 127.0.0.1:%ARACHNID_PORT%:8765"
set "ENVS="
if defined GITHUB_TOKEN set "ENVS=-e GITHUB_TOKEN"

docker run --rm -it %PORTS% %ENVS% -e "ARACHNID_PORT=%ARACHNID_PORT%" -e "ARACHNID_PROJECT_NAME=%PNAME%" -v "%PROJ%:/project" -v "arachnid-%H%-reports:/app/reports" -v "arachnid-%H%-backups:/app/backups" "%IMAGE%" %CMD% %REST%
exit /b %ERRORLEVEL%

:noproject
docker run --rm -it "%IMAGE%" %CMD%
exit /b %ERRORLEVEL%

:update
docker pull "%IMAGE%"
exit /b %ERRORLEVEL%

:nodocker
echo Docker is not installed. Get Docker Desktop: https://www.docker.com/products/docker-desktop/
exit /b 1

:notrunning
echo Docker Desktop is not running. Start it, wait until it says "Engine running", and try again.
exit /b 1
