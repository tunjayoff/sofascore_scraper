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

# Dil: açık ayar (APP_LANGUAGE; ortamda ya da .env'de) > sistem dili (LC_ALL, LC_MESSAGES, LANG; yoksa
# Windows arayüz dili) > İngilizce. Uygulamanın kuralıyla aynı (src\language.py); betik depo
# klonlanmadan önce de çalıştığı için burada yinelenir.
function Get-UiLanguage {
    $value = $env:APP_LANGUAGE
    if ([string]::IsNullOrWhiteSpace($value) -and (Test-Path ".env")) {
        foreach ($line in Get-Content ".env") {
            if ($line -match '^\s*APP_LANGUAGE\s*=\s*["'']?([A-Za-z]+)') { $value = $Matches[1] }
        }
    }
    # Eski LANGUAGE değişkeni yalnızca tam olarak tr / en ise ayar sayılır (GNU gettext de aynı adı kullanır)
    foreach ($candidate in @($value, $env:LANGUAGE)) {
        if ($candidate) {
            $code = $candidate.Trim().ToLowerInvariant()
            if ($code -ceq "tr" -or $code -ceq "en") { return $code }
        }
    }
    foreach ($name in @("LC_ALL", "LC_MESSAGES", "LANG")) {
        $locale = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($locale)) {
            if ($locale.Trim().ToLowerInvariant() -cmatch '^tr($|[_.@-])') { return "tr" }
            return "en"
        }
    }
    if ((Get-UICulture).TwoLetterISOLanguageName -ceq "tr") { return "tr" }
    return "en"
}

$UiLang = Get-UiLanguage

# L "English text" "Türkçe metin"
function L([string]$En, [string]$Tr) {
    if ($UiLang -ceq "tr") { return $Tr }
    return $En
}

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
        throw (L "Git not found. Install it: https://git-scm.com/download/win — then open PowerShell again." `
                "Git bulunamadı. Kurun: https://git-scm.com/download/win — ardından PowerShell'i yeniden açın.")
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
        throw (L "Invalid repository address: '$RepoUrl'. Use https://..., http://... or git@..." `
                "Geçersiz depo adresi: '$RepoUrl'. https://..., http://... veya git@... kullanın.")
    }
    Assert-Git
    if (Test-Path $InstallDir) {
        throw (L "The folder already exists: $InstallDir. Delete it or give another name with -InstallDir." `
                "Klasör zaten var: $InstallDir. Silin veya -InstallDir ile başka bir ad verin.")
    }
    Write-Host (L "→ Cloning the repository: $RepoUrl → $InstallDir" "→ Depo klonlanıyor: $RepoUrl → $InstallDir")
    git clone --depth 1 $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) {
        throw (L "git clone failed — check the network, the URL or your Git configuration." `
                "git clone başarısız — ağ, URL veya Git yapılandırmasını kontrol edin.")
    }
    $root = (Resolve-Path $InstallDir).Path
}

Set-Location $root
# Var olan bir kurulumda .env'deki APP_LANGUAGE de sayılır
$UiLang = Get-UiLanguage
Write-Host (L "→ Project folder: $root" "→ Proje dizini: $root")

$py = Get-PythonCmd
if (-not $py) {
    throw (L "Python not found. Install Python 3.10+: https://www.python.org/downloads/ — tick 'Add python.exe to PATH' in the installer." `
            "Python bulunamadı. Python 3.10+ kurun: https://www.python.org/downloads/ — kurulumda 'Add python.exe to PATH' seçin.")
}

if (-not (Test-Python310 -Exe $py.Exe -PyArgs $py.Args)) {
    throw (L "Python 3.10+ is required. Selected: $($py.Exe) $($py.Args -join ' ') — 'py -0' lists the installed versions." `
            "Python 3.10+ gerekli. Seçilen: $($py.Exe) $($py.Args -join ' ') — 'py -0' ile kurulu sürümleri görebilirsiniz.")
}

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host (L "→ Creating the virtual environment (.venv)…" "→ Sanal ortam oluşturuluyor (.venv)…")
    if ($py.Args.Count -gt 0) {
        & $py.Exe @($py.Args[0], "-m", "venv", ".venv")
    }
    else {
        & $py.Exe -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) {
        throw (L "python -m venv failed — repair the Python installation or try as administrator." `
                "python -m venv başarısız — Python kurulumunu onarın veya yönetici olarak deneyin.")
    }
    if (-not (Test-Path $venvPy)) {
        throw (L ".venv\Scripts\python.exe was not created; installation stopped." `
                ".venv\Scripts\python.exe oluşmadı; kurulum durduruldu.")
    }
}

$pip = Join-Path $root ".venv\Scripts\pip.exe"
Write-Host (L "→ Installing dependencies…" "→ Bağımlılıklar yükleniyor…")
& $venvPy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw (L "pip could not be upgraded — check the proxy / network." "pip güncellenemedi — proxy / ağ kontrol edin.")
}

# constraints.txt: CI'ın test ettiği, birlikte çalıştığı bilinen tam sürümler (dolaylı bağımlılıklar dahil)
& $pip install -r requirements.txt -c constraints.txt
if ($LASTEXITCODE -ne 0) {
    throw (L "pip install -r requirements.txt -c constraints.txt failed — see the error lines above (some packages may need Visual C++ Build Tools)." `
            "pip install -r requirements.txt -c constraints.txt başarısız — üstteki hata satırlarına bakın (bazı paketler için Visual C++ Build Tools gerekebilir).")
}

# Köprü (BrowserBridge) tarayıcıyı Scrapling → patchright ile, channel="chromium" olarak başlatır:
# patchright'ın kendi Chromium derlemesi gerekir. Kurulu Google Chrome KULLANILMAZ; playwright'ın kurulum
# komutu ise yalnızca playwright ve patchright sürümleri denk geldiğinde aynı derlemeyi indirir.
Write-Host (L "→ Installing the browser: patchright's Chromium (the browser the app reaches SofaScore with; a one-time download)…" `
        "→ Tarayıcı kuruluyor: patchright'ın Chromium'u (uygulamanın SofaScore'a eriştiği tarayıcı; tek seferlik indirme)…")
& $venvPy -m patchright install chromium --no-shell
if ($LASTEXITCODE -ne 0) {
    Write-Host (L "Error: the browser could not be installed. Without it the app cannot fetch data from SofaScore (an installed Google Chrome is not used instead)." `
            "Hata: tarayıcı kurulamadı. O olmadan uygulama SofaScore'dan veri çekemez (kurulu Google Chrome onun yerine kullanılmaz).") -ForegroundColor Red
    Write-Host (L "  To try again: `"$venvPy`" -m patchright install chromium --no-shell" `
            "  Yeniden denemek için: `"$venvPy`" -m patchright install chromium --no-shell") -ForegroundColor Red
}

$envFile = Join-Path $root ".env"
$envEx = Join-Path $root ".env.example"
if (-not (Test-Path $envFile) -and (Test-Path $envEx)) {
    Copy-Item $envEx $envFile
    Write-Host (L "→ Copied .env.example → .env." "→ .env.example → .env kopyalandı.")
}

# Web arayüzü Node.js ile bir kez derlenir (frontend\ → frontend\dist\). Sürüm kuralı src\doctor.py'de.
& $venvPy -c "import sys; from src import doctor; sys.exit(0 if doctor.node_is_supported(doctor.installed_node_version()) else 1)"
if ($LASTEXITCODE -eq 0) {
    Write-Host (L "→ Building the web UI (npm install, npm run build; this can take a few minutes)…" `
            "→ Web arayüzü derleniyor (npm install, npm run build; birkaç dakika sürebilir)…")
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
        Write-Host (L "Warning: the web UI could not be built (output above). The terminal modes still work; the web app shows a help page instead of the UI." `
                "Uyarı: web arayüzü derlenemedi (çıktı yukarıda). Terminal modları yine çalışır; web uygulaması arayüz yerine bir yardım sayfası gösterir.") -ForegroundColor Yellow
        Write-Host (L "  To try again: cd `"$root\frontend`" ; npm install ; npm run build" `
                "  Yeniden denemek için: cd `"$root\frontend`" ; npm install ; npm run build") -ForegroundColor Yellow
    }
}
else {
    $nodeFound = (L "none" "yok")
    if (Get-Command node -ErrorAction SilentlyContinue) { $nodeFound = (& node --version) }
    Write-Host (L "Warning: the web UI was not built: Node.js 20.19+ or 22.12+ and npm are required (Node.js found: $nodeFound)." `
            "Uyarı: web arayüzü derlenmedi: Node.js 20.19+ veya 22.12+ ve npm gerekli (bulunan Node.js: $nodeFound).") -ForegroundColor Yellow
    Write-Host (L "  Install Node.js from https://nodejs.org, then run this script again or start with 'Start SofaScore.bat' (it builds the UI too when Node.js is there)." `
            "  Node.js'i https://nodejs.org adresinden kurun, sonra bu betiği yeniden çalıştırın ya da 'Start SofaScore.bat' ile başlatın (Node.js varsa arayüzü o da derler).") -ForegroundColor Yellow
    Write-Host (L "  The terminal UI (python main.py) and headless mode work without Node.js." `
            "  Terminal arayüzü (python main.py) ve headless mod Node.js olmadan çalışır.") -ForegroundColor Yellow
}

Write-Host ""
Write-Host (L "→ Checking the installation (python main.py --doctor)…" "→ Kurulum denetleniyor (python main.py --doctor)…")
Write-Host ""
& $venvPy -m src.doctor
$doctorStatus = $LASTEXITCODE

Write-Host ""
if ($doctorStatus -ne 0) {
    Write-Host (L "The installation is not complete: apply the fixes on the [FAIL] lines above, then check again:" `
            "Kurulum tamamlanmadı: yukarıdaki [FAIL] satırlarındaki çözümleri uygulayın, sonra yeniden denetleyin:") -ForegroundColor Red
    Write-Host "  cd `"$root`" ; .\.venv\Scripts\python.exe main.py --doctor" -ForegroundColor Red
    exit 1
}
Write-Host (L "Installation complete." "Kurulum tamam.") -ForegroundColor Green
Write-Host "  Web:      cd `"$root`" ; .\.venv\Scripts\python.exe scripts\start_web.py  → http://127.0.0.1:8000"
Write-Host "  TUI:      cd `"$root`" ; .\.venv\Scripts\python.exe main.py"
Write-Host "  $(L 'Check:  ' 'Denetim:')  cd `"$root`" ; .\.venv\Scripts\python.exe main.py --doctor"
Write-Host ""
