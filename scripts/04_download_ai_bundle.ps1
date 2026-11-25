# Run on the INTERNET-CONNECTED WINDOWS OFFICE LAPTOP, not the lab server.
# Save 05_install_ai_offline.py beside this script first.
# powershell -NoProfile -ExecutionPolicy Bypass -File .\04_download_ai_bundle.ps1
param([string]$OutputDirectory = (Join-Path $HOME 'Downloads\ran-ai-offline-bundle'))
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$installer = Join-Path $PSScriptRoot '05_install_ai_offline.py'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw 'Save 05_install_ai_offline.py beside this script first.' }
if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { throw 'Windows curl.exe is required.' }
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$bundle = (Resolve-Path -LiteralPath $OutputDirectory).Path
$records = [System.Collections.Generic.List[object]]::new()
function Write-Utf8([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}
function Register-File([string]$Relative, [string]$ExpectedHash) {
    $path = Join-Path $bundle $Relative
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ExpectedHash -and $actual -ne $ExpectedHash.ToLowerInvariant()) { throw "Checksum mismatch: $Relative" }
    $records.Add([ordered]@{path=$Relative.Replace('\','/'); sha256=$actual})
}
function Download-Verified([string]$Url, [string]$Relative, [string]$Hash) {
    if ($Hash -notmatch '^[a-fA-F0-9]{64}$') { throw "Missing upstream SHA256: $Relative" }
    $destination = Join-Path $bundle $Relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    if ((Test-Path -LiteralPath $destination) -and (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -eq $Hash) {
        Write-Host "Already verified: $Relative"
    } else {
        Write-Host "Downloading: $Relative"
        $partial = "$destination.part"
        & curl.exe --fail --location --retry 4 --continue-at - --output $partial $Url
        if ($LASTEXITCODE -ne 0) { throw 'Download interrupted. Rerun the script to resume.' }
        if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Hash) { throw "Downloaded checksum mismatch: $Relative" }
        Move-Item -LiteralPath $partial -Destination $destination -Force
    }
    Register-File $Relative $Hash
}

# Pin the release on first run so interrupted downloads resume against the same version.
$releasePath = Join-Path $bundle 'release.json'
if (-not (Test-Path -LiteralPath $releasePath)) {
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/ollama/ollama/releases/latest' -Headers @{'User-Agent'='ran-ai-offline-builder'}
    Write-Utf8 $releasePath ($release | ConvertTo-Json -Depth 30)
}
$release = Get-Content -LiteralPath $releasePath -Raw | ConvertFrom-Json
$asset = @($release.assets | Where-Object { $_.name -eq 'ollama-linux-amd64.tar.zst' })
if ($asset.Count -ne 1) { throw 'Expected Linux AMD64 .tar.zst release asset not found. Share this error before proceeding.' }
$runtimeHash = ([string]$asset[0].digest) -replace '^sha256:', ''
Download-Verified $asset[0].browser_download_url 'ollama-linux-amd64.tar.zst' $runtimeHash

# Rocky 8 / Python 3.9 compatible decompressor; do not download Windows wheels.
$wheelMetadata = Invoke-RestMethod -Uri 'https://pypi.org/pypi/zstandard/0.23.0/json'
$wheel = @($wheelMetadata.urls | Where-Object { $_.filename -match '^zstandard-0\.23\.0-cp39-cp39-manylinux.*x86_64\.whl$' })
if ($wheel.Count -ne 1) { throw 'Expected one Python 3.9 Linux x86_64 zstandard wheel.' }
Download-Verified $wheel[0].url ("wheels/" + $wheel[0].filename) $wheel[0].digests.sha256

$manifestRelative = 'models/manifests/registry.ollama.ai/library/gpt-oss/20b'
$manifestPath = Join-Path $bundle $manifestRelative
if (-not (Test-Path -LiteralPath $manifestPath)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $manifestPath) | Out-Null
    $manifestPartial = "$manifestPath.part"
    & curl.exe --fail --location --retry 4 --output $manifestPartial 'https://registry.ollama.ai/v2/library/gpt-oss/manifests/20b'
    if ($LASTEXITCODE -ne 0) { throw 'Model manifest download failed.' }
    Get-Content -LiteralPath $manifestPartial -Raw | ConvertFrom-Json | Out-Null
    Move-Item -LiteralPath $manifestPartial -Destination $manifestPath
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
Register-File $manifestRelative ''
foreach ($layer in @($manifest.config) + @($manifest.layers)) {
    if ($layer.digest -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Unsupported model layer digest.' }
    $relative = 'models/blobs/' + $layer.digest.Replace(':','-')
    Download-Verified ("https://registry.ollama.ai/v2/library/gpt-oss/blobs/" + $layer.digest) $relative $layer.digest.Substring(7)
}
Copy-Item -LiteralPath $installer -Destination (Join-Path $bundle '05_install_ai_offline.py') -Force
Register-File '05_install_ai_offline.py' ''
$metadata = [ordered]@{schema=1; ollama_version=$release.tag_name; model='gpt-oss:20b'; files=@($records.ToArray())}
Write-Utf8 (Join-Path $bundle 'bundle.json') ($metadata | ConvertTo-Json -Depth 10)
Write-Host "SUCCESS: $bundle"
Write-Host 'Transfer the ENTIRE folder to $HOME/ran-ai/software/ran-ai-offline-bundle on the AI server.'
Write-Host 'No Windows Ollama installation or Python installation is required on this laptop.'
