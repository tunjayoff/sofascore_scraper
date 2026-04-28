# SofaScore Scraper — kurulum (Windows PowerShell 5.1+ / PowerShell 7+)
#
# Resmi depo: https://github.com/tunjayoff/sofascore_scraper
# Klonlu klasörde: .\scripts\install.ps1
# Sıfırdan: .\scripts\install.ps1 [-RepoUrl https://github.com/.../....git] [-InstallDir sofascore_scraper]

param(
    [string]$RepoUrl = "",
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"

$DefaultRemoteRepo = $(if ($env:SOFASCORE_SCRAPER_DEFAULT_REPO) { $env:SOFASCORE_SCRAPER_DEFAULT_REPO } else { "https://github.com/tunjayoff/sofascore_scraper.git" })

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $InstallDir = $(if ($env:SOFASCORE_SCRAPER_DIR) { $env:SOFASCORE_SCRAPER_DIR } else { "sofascore_scraper" })
}

if ([string]::IsNullOrWhiteSpace($RepoUrl)) {
    $RepoUrl = $(if ($env:SOFASCORE_SCRAPER_REPO) { $env:SOFASCORE_SCRAPER_REPO } else { $DefaultRemoteRepo })
}

function Test-IsRepoUrl([string]$s) {
    if ([string]::IsNullOrWhiteSpace($s)) { return $false }
    return $s.StartsWith("http://") -or $s.StartsWith("https://") -or $s.StartsWith("git@")
}

function Assert-Git {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git bulunamadı. Kurun: https://git-scm.com/download/win — ardından PowerShell'i yeniden açın."
    }
}

function Get-PythonCmd {
    foreach ($name in @("python", "python3", "py")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $cmd) {
            if ($name -eq "py") {
                return @{ Exe = "py"; Args = @("-3") }
            }
            return @{ Exe = $cmd.Source; Args = @() }
        }
    }
    return $null
}

function Test-Python310 {
    param($Exe, $Args)
    & $Exe @Args -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    return $LASTEXITCODE -eq 0
}

$root = $null
if ((Test-Path "requirements.txt") -and (Test-Path "main.py")) {
    $root = (Get-Location).Path
}
elseif ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "..\requirements.txt")) -and (Test-Path (Join-Path $PSScriptRoot "..\main.py"))) {
    $root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

if (-not $root) {
    if (-not (Test-IsRepoUrl $RepoUrl)) {
        throw "Geçersiz depo adresi: '$RepoUrl'. https://..., http://... veya git@... kullanın."
    }
    Assert-Git
    if (Test-Path $InstallDir) {
        throw "Klasör zaten var: $InstallDir. Silin veya -InstallDir ile başka bir ad verin."
    }
    Write-Host "→ Depo klonlanıyor: $RepoUrl → $InstallDir"
    git clone --depth 1 $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) {
        throw "git clone başarısız — ağ, URL veya Git yapılandırmasını kontrol edin."
    }
    $root = (Resolve-Path $InstallDir).Path
}

Set-Location $root
Write-Host "→ Proje dizini: $root"

$py = Get-PythonCmd
if (-not $py) {
    throw "Python bulunamadı. Python 3.10+ kurun: https://www.python.org/downloads/ — kurulumda 'Add python.exe to PATH' seçin."
}

if (-not (Test-Python310 -Exe $py.Exe -Args $py.Args)) {
    throw "Python 3.10+ gerekli. Seçilen: $($py.Exe) $($py.Args -join ' ') — `py -0` ile kurulu sürümleri görebilirsiniz."
}

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "→ Sanal ortam oluşturuluyor (.venv)…"
    if ($py.Args.Count -gt 0) {
        & $py.Exe @($py.Args[0], "-m", "venv", ".venv")
    }
    else {
        & $py.Exe -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) {
        throw "python -m venv başarısız — Python kurulumunu onarın veya yönetici olarak deneyin."
    }
    if (-not (Test-Path $venvPy)) {
        throw ".venv\Scripts\python.exe oluşmadı; kurulum durduruldu."
    }
}

$pip = Join-Path $root ".venv\Scripts\pip.exe"
Write-Host "→ Bağımlılıklar yükleniyor…"
& $venvPy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip güncellenemedi — proxy / ağ kontrol edin."
}

& $pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "pip install -r requirements.txt başarısız — üstteki hata satırlarına bakın (bazı paketler için Visual C++ Build Tools gerekebilir)."
}

$envFile = Join-Path $root ".env"
$envEx = Join-Path $root ".env.example"
if (-not (Test-Path $envFile) -and (Test-Path $envEx)) {
    Copy-Item $envEx $envFile
    Write-Host "→ .env.example → .env kopyalandı."
}

Write-Host ""
Write-Host "Kurulum tamam." -ForegroundColor Green
Write-Host "  Web:  cd `"$root`" ; .\.venv\Scripts\python.exe main.py --web  → http://127.0.0.1:8000"
Write-Host "  TUI:  cd `"$root`" ; .\.venv\Scripts\python.exe main.py"
Write-Host ""
