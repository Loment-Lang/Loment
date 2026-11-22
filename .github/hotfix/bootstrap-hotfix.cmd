@echo off
rem Bootstrap a toolchain hotfix on an installation that predates loment-hotupdate.
rem
rem   1. Compare this script and loment-hotupdate-windows-x64.exe with the digests
rem      published on the release page (out of band).
rem   2. bootstrap-hotfix.cmd PREFIX RELEASE_DIR
rem
rem The updater verifies the Ed25519 signature and every file digest before writing.
setlocal
set "PREFIX=%~1"
set "REL=%~2"
if "%PREFIX%"=="" goto usage
if "%REL%"=="" goto usage

mkdir "%PREFIX%\bin" 2>nul
mkdir "%PREFIX%\share\loment\hot\gen\a\bin" 2>nul
mkdir "%PREFIX%\share\loment\hot\gen\b\bin" 2>nul
copy /y "%REL%\loment-hotupdate-windows-x64.exe" "%PREFIX%\bin\loment-hotupdate.exe" >nul
copy /y "%REL%\release-key.ed25519" "%PREFIX%\share\loment\hot\release-key.ed25519" >nul
copy /y "%REL%\baseline.txt" "%PREFIX%\share\loment\hot\baseline.txt" >nul
rem A stock 0.1.5.1 launcher only runs bin\; install the one that resolves
rem share\loment\hot\current, otherwise the payload lands on disk unused.
copy /y "%REL%\loment-launcher.cmd" "%PREFIX%\bin\loment.cmd" >nul
copy /y "%REL%\loment-launcher.sh" "%PREFIX%\bin\loment" >nul

"%PREFIX%\bin\loment-hotupdate.exe" --prefix "%PREFIX%" --payload "%REL%\payload-windows-x64.hot" --sig "%REL%\payload-windows-x64.hot.sig"
exit /b %ERRORLEVEL%

:usage
echo usage: bootstrap-hotfix.cmd PREFIX RELEASE_DIR 1>&2
exit /b 2
