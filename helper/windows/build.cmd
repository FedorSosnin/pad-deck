@echo off
rem Builds PadDeckHelper.exe with the C# compiler that comes with Windows (.NET Framework 4). No installs needed.
rem The web app (see ..\web-files.txt) is packed into web.zip and built into the .exe.
setlocal
pushd "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File make-web-zip.ps1
if errorlevel 1 (popd & exit /b 1)
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe
"%CSC%" /nologo /optimize /target:winexe /win32icon:..\icon\PadDeckHelper.ico /out:PadDeckHelper.exe ^
  /resource:web.zip,web.zip /resource:..\..\VERSION,VERSION ^
  /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll /r:System.Core.dll ^
  /r:System.IO.Compression.dll ^
  *.cs
set RESULT=%ERRORLEVEL%
popd
exit /b %RESULT%
