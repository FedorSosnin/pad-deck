# Packs the web app (the files listed in ..\web-files.txt) into web.zip, which build.cmd puts inside the .exe.
# Entry names use forward slashes, so the Helper finds them the same way the Mac app does.
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$zipPath = Join-Path $PSScriptRoot "web.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath }
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, "Create")
try {
  foreach ($line in Get-Content (Join-Path $PSScriptRoot "..\web-files.txt")) {
    $item = $line.Trim()
    if (-not $item -or $item.StartsWith("#")) { continue }
    $full = Join-Path $root $item
    if (Test-Path $full -PathType Container) {
      $files = Get-ChildItem $full -Recurse -File
      if ($item -eq "icons") { $files = $files | Where-Object { $_.Name -like "deck-*.svg" } }
    } else { $files = @(Get-Item $full) }
    foreach ($f in $files) {
      $name = $f.FullName.Substring($root.Length + 1).Replace("\", "/")
      [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $f.FullName, $name) | Out-Null
    }
  }
} finally { $zip.Dispose() }
Write-Host "Made web.zip"
