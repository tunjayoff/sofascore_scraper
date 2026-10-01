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
    # py (Python Launcher) önce: "python" Microsoft Store yönlendirme kısayolu olabilir
    foreach ($name in @("py", "python", "python3")) {
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
    # $Args PowerShell'in otomatik değişkeni: parametre adı olarak kullanılamaz
    param($Exe, $PyArgs)
    & $Exe @PyArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
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

if (-not (Test-Python310 -Exe $py.Exe -PyArgs $py.Args)) {
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

# Köprü (BrowserBridge) tarayıcıyı Scrapling → patchright ile, channel="chromium" olarak başlatır:
# patchright'ın kendi Chromium derlemesi gerekir. Kurulu Google Chrome KULLANILMAZ; playwright'ın kurulum
# komutu ise yalnızca playwright ve patchright sürümleri denk geldiğinde aynı derlemeyi indirir.
Write-Host "→ Tarayıcı kuruluyor: patchright'ın Chromium'u (uygulamanın SofaScore'a eriştiği tarayıcı; tek seferlik indirme)…"
& $venvPy -m patchright install chromium --no-shell
if ($LASTEXITCODE -ne 0) {
    Write-Host "Hata: tarayıcı kurulamadı. O olmadan uygulama SofaScore'dan veri çekemez (kurulu Google Chrome onun yerine kullanılmaz)." -ForegroundColor Red
    Write-Host "  Yeniden denemek için: `"$venvPy`" -m patchright install chromium --no-shell" -ForegroundColor Red
}

$envFile = Join-Path $root ".env"
$envEx = Join-Path $root ".env.example"
if (-not (Test-Path $envFile) -and (Test-Path $envEx)) {
    Copy-Item $envEx $envFile
    Write-Host "→ .env.example → .env kopyalandı."
}

# Web arayüzü Node.js ile bir kez derlenir (frontend\ → frontend\dist\). Sürüm kuralı src\doctor.py'de.
& $venvPy -c "import sys; from src import doctor; sys.exit(0 if doctor.node_is_supported(doctor.installed_node_version()) else 1)"
if ($LASTEXITCODE -eq 0) {
    Write-Host "→ Web arayüzü derleniyor (npm install, npm run build; birkaç dakika sürebilir)…"
    $uiBuilt = $false
    Push-Location (Join-Path $root "frontend")
    try {
        & npm install
        if ($LASTEXITCODE -eq 0) {
            & npm run build
            $uiBuilt = ($LASTEXITCODE -eq 0)
        }
    }
    finally {
        Pop-Location
    }
    if (-not $uiBuilt) {
        Write-Host "Uyarı: web arayüzü derlenemedi (çıktı yukarıda). Terminal modları yine çalışır; web uygulaması arayüz yerine bir yardım sayfası gösterir." -ForegroundColor Yellow
        Write-Host "  Yeniden denemek için: cd `"$root\frontend`" ; npm install ; npm run build" -ForegroundColor Yellow
    }
}
else {
    $nodeFound = "yok"
    if (Get-Command node -ErrorAction SilentlyContinue) { $nodeFound = (& node --version) }
    Write-Host "Uyarı: web arayüzü derlenmedi: Node.js 20.19+ veya 22.12+ ve npm gerekli (bulunan Node.js: $nodeFound)." -ForegroundColor Yellow
    Write-Host "  Node.js'i https://nodejs.org adresinden kurun, sonra bu betiği yeniden çalıştırın ya da 'Start SofaScore.bat' ile başlatın (Node.js varsa arayüzü o da derler)." -ForegroundColor Yellow
    Write-Host "  Terminal arayüzü (python main.py) ve headless mod Node.js olmadan çalışır." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "→ Kurulum denetleniyor (python main.py --doctor)…"
Write-Host ""
& $venvPy -m src.doctor
$doctorStatus = $LASTEXITCODE

Write-Host ""
if ($doctorStatus -ne 0) {
    Write-Host "Kurulum tamamlanmadı: yukarıdaki [FAIL] satırlarındaki çözümleri uygulayın, sonra yeniden denetleyin:" -ForegroundColor Red
    Write-Host "  cd `"$root`" ; .\.venv\Scripts\python.exe main.py --doctor" -ForegroundColor Red
    exit 1
}
Write-Host "Kurulum tamam." -ForegroundColor Green
Write-Host "  Web:      cd `"$root`" ; .\.venv\Scripts\python.exe scripts\start_web.py  → http://127.0.0.1:8000"
Write-Host "  TUI:      cd `"$root`" ; .\.venv\Scripts\python.exe main.py"
Write-Host "  Denetim:  cd `"$root`" ; .\.venv\Scripts\python.exe main.py --doctor"
Write-Host ""
