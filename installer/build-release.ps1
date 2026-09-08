param(
    [ValidatePattern('^\d+\.\d+\.\d+-pretest\.\d+$')]
    [string]$Version = '0.2.0-pretest.1',
    [string]$Python = 'build/release-venv/Scripts/python.exe',
    [string]$Compiler = 'build/tools/inno/ISCC.exe'
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$releaseRoot = Join-Path $PWD "dist/releases/v$Version"
if (Test-Path -LiteralPath $releaseRoot) { throw 'Release directory already exists; use a new version or review the existing build manually.' }
New-Item -ItemType Directory -Path $releaseRoot | Out-Null
$payloadRoot = Join-Path $releaseRoot 'payload'
& $Python -m PyInstaller --noconfirm --distpath $payloadRoot --workpath "build/release-$Version" YouShuLedgerAI.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
$appRoot = Join-Path $payloadRoot 'YouShuLedgerAI'
Copy-Item -LiteralPath 'installer/使用前必读.txt' -Destination $appRoot
$forbidden = Get-ChildItem -LiteralPath $appRoot -Recurse -File | Where-Object { $_.Name -in @('.env','.model_key','.secret_key','.instance_id') -or $_.Extension -in @('.db','.sqlite','.sqlite3') }
if ($forbidden) { throw 'Sensitive runtime files detected in payload; release blocked.' }
& $Compiler "/DMyAppVersion=$Version" "/DPayloadDir=$appRoot" "/O$releaseRoot" 'installer/YouShuLedgerAI.iss'
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
$zipPath = Join-Path $releaseRoot "YouShuLedgerAI-portable-v$Version-win-x64.zip"
Compress-Archive -Path $appRoot -DestinationPath $zipPath -CompressionLevel Optimal
$assets = Get-ChildItem -LiteralPath $releaseRoot -File | Where-Object { $_.Extension -in @('.exe','.zip') }
$hashLines = $assets | ForEach-Object { ((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() + '  ' + $_.Name) }
[IO.File]::WriteAllLines((Join-Path $releaseRoot 'SHA256SUMS.txt'), $hashLines, [Text.UTF8Encoding]::new($false))
& $Python -m pip freeze | Set-Content -LiteralPath (Join-Path $releaseRoot 'build-dependencies.txt') -Encoding utf8
Write-Output "Build ready for isolated acceptance and secret audit: $releaseRoot"
