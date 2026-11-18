@echo off
rem Builds PadDeckHelper.exe with the C# compiler that comes with Windows (.NET Framework 4). No installs needed.
setlocal
pushd "%~dp0"
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe
"%CSC%" /nologo /optimize /target:winexe /win32icon:..\icon\PadDeckHelper.ico /out:PadDeckHelper.exe ^
  /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll /r:System.Core.dll ^
  PadDeckHelper.cs
set RESULT=%ERRORLEVEL%
popd
exit /b %RESULT%
