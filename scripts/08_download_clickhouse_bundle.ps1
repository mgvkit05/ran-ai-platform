# Run on the internet-connected Windows laptop. Save 09_install_clickhouse_offline.py beside this file.
param(
    [string]$OutputDirectory = (Join-Path $HOME 'Downloads\ran-ai-clickhouse-bundle'),
    [switch]$AllowOfflineRevocationCheck
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$installer = Join-Path $PSScriptRoot '09_install_clickhouse_offline.py'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw 'Save 09_install_clickhouse_offline.py beside this script.' }
if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { throw 'Windows curl.exe is required.' }
$curlOptions = @()
if ($AllowOfflineRevocationCheck) {
    if (-not ((& curl.exe --help all) -match '--ssl-revoke-best-effort')) { throw 'This curl lacks --ssl-revoke-best-effort.' }
    $curlOptions = @('--ssl-revoke-best-effort')
    Write-Warning 'Unavailable certificate revocation endpoints will not block curl; TLS certificate and checksum verification remain enabled.'
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$bundle = (Resolve-Path -LiteralPath $OutputDirectory).Path
function Write-Utf8([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}
$releasePath = Join-Path $bundle 'release.json'
if (-not (Test-Path -LiteralPath $releasePath)) {
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/ClickHouse/ClickHouse/releases/latest' -Headers @{'User-Agent'='ran-ai-offline-builder'}
    Write-Utf8 $releasePath ($release | ConvertTo-Json -Depth 30)
}
$release = Get-Content -LiteralPath $releasePath -Raw | ConvertFrom-Json
if ($release.tag_name -notmatch '^v[0-9.]+-(stable|lts)$') { throw 'Expected an official stable or LTS release.' }
$assets = @($release.assets | Where-Object { $_.name -match '^clickhouse-common-static-[0-9.]+-amd64\.tgz$' })
if ($assets.Count -ne 1) { throw 'Expected one Linux AMD64 common-static package.' }
$asset = $assets[0]
$checksum = ([string]$asset.digest) -replace '^sha256:', ''
if ($checksum -notmatch '^[a-fA-F0-9]{64}$') { throw 'Upstream SHA256 digest is unavailable. Stop and share this error.' }
$package = Join-Path $bundle $asset.name
if ((Test-Path -LiteralPath $package) -and (Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash -eq $checksum) {
    Write-Host 'Reusing verified ClickHouse package.'
} else {
    Write-Host ('Downloading ' + $asset.name)
    $partial = "$package.part"
    & curl.exe @curlOptions --fail --location --retry 4 --continue-at - --output $partial $asset.browser_download_url
    if ($LASTEXITCODE -ne 0) { throw 'Download interrupted. Rerun to resume. For offline certificate revocation errors add -AllowOfflineRevocationCheck.' }
    if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $checksum) { throw 'Package SHA256 mismatch. Nothing will be installed.' }
    Move-Item -LiteralPath $partial -Destination $package -Force
}
$copiedInstaller = Join-Path $bundle '09_install_clickhouse_offline.py'
Copy-Item -LiteralPath $installer -Destination $copiedInstaller -Force
$metadata = [ordered]@{
    schema=1
    version=$release.tag_name
    package=$asset.name
    sha256=$checksum.ToLowerInvariant()
    installer_sha256=(Get-FileHash -LiteralPath $copiedInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
}
Write-Utf8 (Join-Path $bundle 'clickhouse-bundle.json') ($metadata | ConvertTo-Json)
Write-Host "SUCCESS: $bundle"
Write-Host 'Transfer the ENTIRE folder to $HOME/ran-ai/software/ran-ai-clickhouse-bundle on the AI server.'
Write-Host 'This download does not install or start ClickHouse on the laptop.'
