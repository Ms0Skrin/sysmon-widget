# Сборка установщика dist\SysMonWidget-Setup.exe
#   .\build.ps1            (нужны Python + requirements.txt и Inno Setup 6)
param([string]$Version = "")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $Version) { $Version = (git -C $root describe --tags --always).Trim().TrimStart("v") }
$build = Join-Path $root "build"
$cache = Join-Path $build "cache"
$tp = Join-Path $build "third_party"
New-Item -ItemType Directory -Force $cache | Out-Null

# Сторонние программы: фиксированные версии и SHA-256
$pinned = @(
  @{ Name = "nabyscape"; File = "NabyScape-win-x64-Portable.zip"; Ver = "v2.26.0"
     Url = "https://github.com/BillyNabil/nabyscape/releases/download/v2.26.0/NabyScape-win-x64-Portable.zip"
     Sha = "7e660e1d6486a6ddd89484063f525b71735ffefdc29cca79011dae8fed2e65bc" },
  @{ Name = "translucenttb"; File = "TranslucentTB-portable-x64.zip"; Ver = "2026.2"
     Url = "https://github.com/TranslucentTB/TranslucentTB/releases/download/2026.2/TranslucentTB-portable-x64.zip"
     Sha = "0dbe8e0255c20e131cde536dcd0ae490d45989a7d360e26d0150dff1922ac420" },
  @{ Name = "pawnio"; File = "PawnIO_setup.exe"; Ver = "2.2.0"
     Url = "https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe"
     Sha = "1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032" }
)
Remove-Item -Recurse -Force $tp -ErrorAction SilentlyContinue
foreach ($p in $pinned) {
  $file = Join-Path $cache $p.File
  if (-not (Test-Path $file)) {
    Write-Host "Download $($p.File)"
    Invoke-WebRequest $p.Url -OutFile $file -UseBasicParsing
  }
  $hash = (Get-FileHash $file -Algorithm SHA256).Hash.ToLower()
  if ($hash -ne $p.Sha) { Remove-Item $file; throw "SHA-256 mismatch: $($p.File)" }
  $dest = Join-Path $tp $p.Name
  New-Item -ItemType Directory -Force $dest | Out-Null
  if ($file.EndsWith(".zip")) { Expand-Archive $file -DestinationPath $dest -Force } else { Copy-Item $file $dest }
  Set-Content -Path (Join-Path $dest "version.txt") -Value $p.Ver -Encoding utf8
}

# NabyScape: собираем русскую версию из vendor/nabyscape (нужен .NET 10 SDK);
# без SDK остаётся официальная сборка из архива выше
$nabySrc = Join-Path $root "vendor\nabyscape"
if ((Get-Command dotnet -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $nabySrc "build.ps1"))) {
  Write-Host "NabyScape (ru) from source"
  $nabyOut = Join-Path $build "nabyscape-ru"
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $nabySrc "build.ps1") -OutputDirectory $nabyOut -SkipTests
  if ($LASTEXITCODE) { throw "NabyScape build failed" }
  $dest = Join-Path $tp "nabyscape"
  Remove-Item -Recurse -Force $dest -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force $dest | Out-Null
  Get-ChildItem $nabyOut -File | Where-Object { $_.Name -notlike "*Setup*.exe" -and $_.Name -notlike "unit-report*" } |
    Copy-Item -Destination $dest
  Set-Content -Path (Join-Path $dest "version.txt") -Value "v2.26.0-ru" -Encoding utf8
}

Write-Host "Installer art"
python (Join-Path $root "packaging\installer_art.py") (Join-Path $build "art")
if ($LASTEXITCODE) { throw "installer art failed" }

Write-Host "PyInstaller"
# Переменные Tcl/Tk, унаследованные от запущенного собранного виджета, ломают поиск tkinter
Remove-Item Env:TCL_LIBRARY, Env:TK_LIBRARY -ErrorAction SilentlyContinue
python -m PyInstaller (Join-Path $root "packaging\sysmon.spec") --noconfirm --clean `
  --distpath (Join-Path $root "dist") --workpath (Join-Path $build "pyi")
if ($LASTEXITCODE) { throw "PyInstaller failed" }
if (-not (Test-Path (Join-Path $root "dist\SysMonWidget\_internal\_tcl_data\init.tcl"))) { throw "tkinter не попал в сборку" }
$dist = Join-Path $root "dist\SysMonWidget"
Set-Content -Path (Join-Path $dist "version.txt") -Value $Version -Encoding utf8
Copy-Item (Join-Path $root "vendor\lhm") (Join-Path $dist "lhm") -Recurse -Force
New-Item -ItemType Directory -Force (Join-Path $dist "assets") | Out-Null
Copy-Item (Join-Path $root "assets\app.ico") (Join-Path $dist "assets") -Force

Write-Host "Inno Setup"
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
$env:SYSMON_VERSION = $Version
# пишем в build\out: dist\SysMonWidget-Setup.exe может быть занят запущенным установщиком
$out = Join-Path $build "out"
& $iscc /Q "/O$out" (Join-Path $root "packaging\installer.iss")
if ($LASTEXITCODE) { throw "ISCC failed" }
$setup = Join-Path $out "SysMonWidget-Setup.exe"
try { Copy-Item $setup (Join-Path $root "dist") -Force -ErrorAction Stop } catch { Write-Host "dist copy skipped (file in use)" }
Get-Item $setup | Select-Object FullName, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
