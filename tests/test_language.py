"""
Dil kuralı: açık ayar (`display.language`: SOFASCORE_DISPLAY__LANGUAGE) > sistem dili > İngilizce.

Kural sofascore_scraper/language.py'de durur; kurulum ve başlatma betikleri (bash, PowerShell) Python daha
yokken çalıştıkları için kendi kurallarını uygular. Betiklerin Python'suz yedek kuralı 2.x'in APP_LANGUAGE adını
okur: kurulum betikleri (scripts/install.*) P30'un kapsamında değildir; betiklere kendi tablosu sorulur
(SCRIPT_CASES). Başlatma betikleri Python varken dili uygulamaya sorar (app_lang, aşağıda).
Ayrıca: iki dil dosyasında aynı anahtarlar var mı, yeni kurulum İngilizce mi başlıyor.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sofascore_scraper import doctor, language
from sofascore_scraper.i18n import I18nManager, app_language

REPO = Path(__file__).resolve().parents[1]
BASH_SCRIPTS = ("scripts/install.sh", "start-sofascore.sh", "Start SofaScore.command")

# (ortam, .env içeriği ya da None, beklenen dil)
CASES = [
    ({}, None, "en"),
    ({"LANG": "tr_TR.UTF-8"}, None, "tr"),
    ({"LANG": "tr"}, None, "tr"),
    ({"LANG": "TR_tr"}, None, "tr"),
    ({"LANG": "en_US.UTF-8"}, None, "en"),
    ({"LANG": "de_DE.UTF-8"}, None, "en"),  # desteklenmeyen dil: İngilizce
    ({"LANG": "C"}, None, "en"),
    ({"LANG": "trx_XX"}, None, "en"),  # "tr" ile başlayan başka bir kod Türkçe değildir
    # POSIX önceliği: LC_ALL > LC_MESSAGES > LANG; ilk dolu olan belirler
    ({"LANG": "tr_TR.UTF-8", "LC_ALL": "C"}, None, "en"),
    ({"LANG": "en_US.UTF-8", "LC_MESSAGES": "tr_TR.UTF-8"}, None, "tr"),
    ({"LANG": "tr_TR.UTF-8", "LC_MESSAGES": "en_US.UTF-8", "LC_ALL": "tr_TR.UTF-8"}, None, "tr"),
    # Açık ayar her zaman kazanır
    ({"LANG": "tr_TR.UTF-8", "SOFASCORE_DISPLAY__LANGUAGE": "en"}, None, "en"),
    ({"LANG": "en_US.UTF-8", "SOFASCORE_DISPLAY__LANGUAGE": "tr"}, None, "tr"),
    ({"LANG": "en_US.UTF-8", "SOFASCORE_DISPLAY__LANGUAGE": "TR"}, None, "tr"),
    ({"LANG": "tr_TR.UTF-8", "SOFASCORE_DISPLAY__LANGUAGE": "de"}, None, "tr"),  # desteklenmeyen ayar yok sayılır
    # 2.x'in LANGUAGE ve APP_LANGUAGE adları 3.1'de okunmaz (P30); LANGUAGE gettext'in değişkenidir
    ({"LANG": "en_US.UTF-8", "LANGUAGE": "tr"}, None, "en"),
    ({"LANG": "en_US.UTF-8", "LANGUAGE": "tr_TR:tr"}, None, "en"),
    ({"LANG": "en_US.UTF-8", "APP_LANGUAGE": "tr"}, None, "en"),
    # .env'deki ayar
    ({"LANG": "en_US.UTF-8"}, "SOFASCORE_CLIENT__MAX_CONCURRENT=5\nSOFASCORE_DISPLAY__LANGUAGE=tr\n", "tr"),
    ({"LANG": "tr_TR.UTF-8"}, 'SOFASCORE_DISPLAY__LANGUAGE="en"\n', "en"),
    ({"LANG": "tr_TR.UTF-8"}, "SOFASCORE_DISPLAY__LANGUAGE=\n", "tr"),  # boş = ayarlanmamış
    ({"LANG": "en_US.UTF-8", "SOFASCORE_DISPLAY__LANGUAGE": "en"}, "SOFASCORE_DISPLAY__LANGUAGE=tr\n", "en"),  # süreç ortamı .env'in önünde
    ({"LANG": "en_US.UTF-8"}, "APP_LANGUAGE=tr\n", "en"),
]
_IDS = [f"{i}-{expected}" for i, (_env, _file, expected) in enumerate(CASES)]

# Python'dan önce çalışan betiklerin yedek kuralı (scripts/install.sh, install.ps1 ve başlatma betiklerinin
# detect_lang'ı): 2.x'in adları. Kurulum betikleri P30'un kapsamında değildir (bkz. modülün açıklaması).
SCRIPT_CASES = [case for case in CASES[:11]] + [
    ({"LANG": "tr_TR.UTF-8", "APP_LANGUAGE": "en"}, None, "en"),
    ({"LANG": "en_US.UTF-8", "APP_LANGUAGE": "tr"}, None, "tr"),
    ({"LANG": "en_US.UTF-8", "APP_LANGUAGE": "TR"}, None, "tr"),
    ({"LANG": "tr_TR.UTF-8", "APP_LANGUAGE": "de"}, None, "tr"),
    ({"LANG": "en_US.UTF-8", "LANGUAGE": "tr"}, None, "tr"),
    ({"LANG": "en_US.UTF-8", "LANGUAGE": "tr_TR:tr"}, None, "en"),
    ({"LANG": "en_US.UTF-8"}, "MAX_CONCURRENT=5\nAPP_LANGUAGE=tr\n", "tr"),
    ({"LANG": "tr_TR.UTF-8"}, 'APP_LANGUAGE="en"\n', "en"),
    ({"LANG": "tr_TR.UTF-8"}, "APP_LANGUAGE=\n", "tr"),
    ({"LANG": "en_US.UTF-8", "APP_LANGUAGE": "en"}, "APP_LANGUAGE=tr\n", "en"),
]
_SCRIPT_IDS = [f"{i}-{expected}" for i, (_env, _file, expected) in enumerate(SCRIPT_CASES)]


# --- Python: tek kural ------------------------------------------------------------------------


@pytest.mark.parametrize("environ,env_text,expected", CASES, ids=_IDS)
def test_rule_in_python(tmp_path, environ, env_text, expected):
    if env_text is None:
        assert language.resolve_language(environ, platform="linux") == expected
    else:
        (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    # --doctor ve başlatıcı .env'i de okur (süreç ortamı önce)
    assert doctor.Context(root=tmp_path, environ=environ, platform="linux").lang == expected


def test_language_of_reads_locale_names():
    assert [language.language_of(v) for v in ("tr_TR.UTF-8", "tr-TR", "TR", "tr@euro", "en_GB", " en ")] == [
        "tr", "tr", "tr", "tr", "en", "en",
    ]
    assert [language.language_of(v) for v in ("", None, "C", "POSIX", "de_DE", "trk", "C.UTF-8")] == [None] * 7


def test_explicit_and_detected_are_reported_separately():
    assert language.explicit_language({"LANG": "tr_TR.UTF-8"}) is None  # sistem dili ayar değildir
    assert language.explicit_language({"SOFASCORE_DISPLAY__LANGUAGE": "tr"}) == "tr"
    assert language.explicit_language({"SOFASCORE_DISPLAY__LANGUAGE": ""}) is None
    assert language.detected_language({"SOFASCORE_DISPLAY__LANGUAGE": "en", "LANG": "tr_TR.UTF-8"}, platform="linux") == "tr"
    assert language.detected_language({}, platform="linux") is None


def test_windows_falls_back_to_the_user_interface_language(monkeypatch):
    ui = {"value": "tr_TR"}
    monkeypatch.setattr(language, "_windows_ui_language", lambda: ui["value"])
    assert language.resolve_language({}, platform="win32") == "tr"
    assert language.resolve_language({"SOFASCORE_DISPLAY__LANGUAGE": "en"}, platform="win32") == "en"
    # Git Bash gibi kabuklar LANG verir: o zaman o belirler
    assert language.resolve_language({"LANG": "en_US.UTF-8"}, platform="win32") == "en"
    for other in ("en_US", "de_DE", None):
        ui["value"] = other
        assert language.resolve_language({}, platform="win32") == "en"
    # Windows dışında arayüz dili hiç sorulmaz
    monkeypatch.setattr(language, "_windows_ui_language", lambda: pytest.fail("yalnızca Windows'ta"))
    assert language.resolve_language({}, platform="linux") == "en"
    assert language.resolve_language({}, platform="darwin") == "en"


def test_windows_ui_language_lookup_never_raises():
    assert language._windows_ui_language() is None or isinstance(language._windows_ui_language(), str)


def test_the_app_uses_the_rule(monkeypatch):
    for key in language.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(language, "_windows_ui_language", lambda: None)
    assert app_language() == "en" and I18nManager().current_lang == "en"
    monkeypatch.setenv("LANG", "tr_TR.UTF-8")
    assert app_language() == "tr" and I18nManager().current_lang == "tr"
    monkeypatch.setenv("SOFASCORE_DISPLAY__LANGUAGE", "en")
    assert app_language() == "en" and I18nManager().current_lang == "en"


# --- kabuk betikleri: aynı kural ---------------------------------------------------------------


def _bash_function(script: str) -> str:
    text = (REPO / script).read_text(encoding="utf-8")
    match = re.search(r"^detect_lang\(\) \{\n.*?^\}\n", text, flags=re.S | re.M)
    assert match, f"{script} has no detect_lang()"
    return match.group(0)


def test_shell_scripts_share_one_detect_lang():
    """Üç betik de Python'dan önce çalışır; işlev kopyalanır ve kopyalar aynı kalmalıdır."""
    assert len({_bash_function(script) for script in BASH_SCRIPTS}) == 1


@pytest.mark.skipif(os.name == "nt", reason="bash betikleri POSIX'te çalışır")
@pytest.mark.parametrize("environ,env_text,expected", SCRIPT_CASES, ids=_SCRIPT_IDS)
def test_rule_in_the_shell_scripts(tmp_path, environ, env_text, expected):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash yok")
    if env_text is not None:
        (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    proc = subprocess.run(
        [bash, "-c", "set -euo pipefail\n" + _bash_function(BASH_SCRIPTS[0]) + "detect_lang\n"],
        cwd=str(tmp_path), env={"PATH": os.environ.get("PATH", ""), **environ},
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected


@pytest.mark.parametrize("script", BASH_SCRIPTS)
def test_shell_scripts_say_everything_in_both_languages(script):
    text = (REPO / script).read_text(encoding="utf-8")
    assert 'UI_LANG="$(detect_lang)"' in text
    # Kullanıcıya giden metin msg "English" "Türkçe" ile yazılır: yalnızca Türkçe yazan echo kalmamalı
    # (dilden bağımsız satırlar, ör. bir komut örneği, echo ile kalabilir)
    turkish_only = [ln for ln in text.splitlines() if re.match(r"\s*echo\s.*[çğıöşüÇĞİÖŞÜ]", ln)]
    assert turkish_only == [], turkish_only
    assert text.count('msg "') >= 3


# Yürütme ilkesi yalnızca Windows'ta vardır (install.bat de betiği böyle çağırır)
_POLICY = ("-ExecutionPolicy", "Bypass") if os.name == "nt" else ()


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _run_powershell(tmp_path: Path, body: str, environ: dict) -> subprocess.CompletedProcess:
    script = tmp_path / "probe.ps1"
    script.write_text(body, encoding="utf-8-sig")  # BOM: Windows PowerShell 5.1 dosyayı UTF-8 okusun
    env = {k: v for k, v in os.environ.items() if k not in language.ENV_KEYS + ("APP_LANGUAGE", "LANGUAGE")}
    env.update(environ)
    return subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", *_POLICY, "-File", str(script)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120,
    )


def _powershell_function() -> str:
    text = (REPO / "scripts" / "install.ps1").read_text(encoding="utf-8-sig")
    match = re.search(r"^function Get-UiLanguage \{\r?\n.*?^\}\r?\n", text, flags=re.S | re.M)
    assert match, "install.ps1 has no Get-UiLanguage"
    return match.group(0)


def test_powershell_installer_parses(tmp_path):
    """Sözdizimi denetimi: betik çalıştırılmaz (kurulum yapılmaz), yalnızca ayrıştırılır."""
    if _powershell() is None:
        pytest.skip("PowerShell yok")
    target = str(REPO / "scripts" / "install.ps1").replace("'", "''")
    proc = _run_powershell(
        tmp_path,
        "$errors = $null\n"
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{target}', [ref]$null, [ref]$errors)\n"
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Output $_.ToString() }; exit 1 }\n"
        "Write-Output 'parsed'\n",
        {},
    )
    assert proc.returncode == 0 and "parsed" in proc.stdout, proc.stdout + proc.stderr


# Ortam hiçbir şey söylemiyorsa PowerShell makinenin arayüz diline bakar: o örnek makineye bağlıdır
@pytest.mark.parametrize(
    "environ,env_text,expected", [c for c in SCRIPT_CASES if c[0]],
    ids=[f"{i}-{c[2]}" for i, c in enumerate(SCRIPT_CASES) if c[0]],
)
def test_rule_in_the_powershell_installer(tmp_path, environ, env_text, expected):
    if _powershell() is None:
        pytest.skip("PowerShell yok")
    if env_text is not None:
        (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    proc = _run_powershell(tmp_path, _powershell_function() + "\nGet-UiLanguage\n", environ)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == expected


def test_windows_scripts_keep_their_encoding_and_line_endings():
    ps1 = (REPO / "scripts" / "install.ps1").read_bytes()
    assert ps1.startswith(b"\xef\xbb\xbf"), "install.ps1 must keep its UTF-8 BOM (Windows PowerShell 5.1)"
    for name in ("scripts/install.ps1", "scripts/install.bat", "Start SofaScore.bat"):
        data = (REPO / name).read_bytes()
        assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), f"{name} must use CRLF"
    start = (REPO / "Start SofaScore.bat").read_bytes()
    start.decode("ascii")  # kod sayfasından bağımsız: yalnızca ASCII
    assert b"Python not found" in start and b"Python bulunamadi" in start and b"APP_LANGUAGE" in start
    text = ps1.decode("utf-8-sig")
    assert "$UiLang = Get-UiLanguage" in text
    # Kullanıcıya giden metinler L "English" "Türkçe" ile seçilir
    assert not re.findall(r'(?m)^\s*throw\s+"', text)
    assert not re.findall(r'(?m)^\s*Write-Host\s+"[^"]*[çğıöşüİ]', text)


# --- dil dosyaları -----------------------------------------------------------------------------


def _locale(lang: str) -> dict:
    with open(REPO / "locales" / f"{lang}.json", encoding="utf-8") as f:
        return json.load(f)


def _placeholders(text: str) -> set:
    return set(re.findall(r"\{(\w+)\}", text))


def test_cli_locale_files_have_the_same_keys_and_placeholders():
    en, tr = _locale("en"), _locale("tr")
    assert set(en) == set(tr), sorted(set(en) ^ set(tr))
    for key in en:
        assert isinstance(en[key], str) and en[key].strip(), key
        assert isinstance(tr[key], str) and tr[key].strip(), key
        assert _placeholders(en[key]) == _placeholders(tr[key]), key


def test_keys_used_by_the_cli_and_the_launcher_exist():
    en = _locale("en")
    used = set()
    for name in ("sofascore_scraper/bridge_health.py",):  # 2.x'in indiricileri P30'da kalktı
        used |= set(re.findall(r"""\bt\(\s*['"]([a-z0-9_]+)['"]""", (REPO / name).read_text(encoding="utf-8")))
    launcher = (REPO / "scripts" / "start_web.py").read_text(encoding="utf-8")
    used |= {"launcher_" + key for key in re.findall(r'_t\(\s*"([a-z_]+)"', launcher)}
    # P25: main.py'nin web sunucusu dalı `ssc serve` oldu; P30: main.py'nin bayrakları ve metinleri gitti
    assert len(used) > 20
    assert used <= set(en), sorted(used - set(en))
    # Kullanılmayan çeviri kalmasın (bu değişiklikle eklenen ön ekler için)
    doctor_text = (REPO / "sofascore_scraper" / "doctor.py").read_text(encoding="utf-8")
    # `details_*` anahtarlarını P15'e kadar MatchDataFetcher yazdırıyordu; artık günlük satırıdırlar ve anahtarlar
    # terminal menüsüyle (P26) kullanım taramasından sonra silinir
    for key in en:
        if key.startswith("launcher_"):
            assert f'"{key[len("launcher_"):]}"' in launcher, key
        elif key.startswith("cli_"):
            assert f'"{key}"' in doctor_text, key


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_help_texts_survive_argparse_formatting(lang):
    """argparse yardım metinlerini %-biçimlendirir: yalın bir "%" --help'i çökertir."""
    for key, text in _locale(lang).items():
        if key.startswith(("cli_help_", "cli_description", "cli_epilog", "doctor_cli_")):
            assert text.replace("{ids}", "").replace("{valid}", "") % {"prog": "main.py"}, key
            assert not re.search(r"\{(?!ids\}|valid\})", text), key  # str.format: süslü ayraç yok


@pytest.mark.parametrize("lang,expected,absent", [
    ("en", ["Command line of the SofaScore data platform", "The web app: python main.py serve."], "arayüz"),
    ("tr", ["SofaScore veri platformunun komut satırı", "Web uygulaması: python main.py serve."], "Command line"),
])
def test_main_help_is_in_the_chosen_language(capsys, lang, expected, absent, restore_cli_process):
    import main as cli

    assert cli.main(["--lang", lang, "--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: python main.py")
    for text in expected:
        assert text in out, text
    assert absent not in out and "ssc_help_" not in out  # çevrilmemiş anahtar görünmüyor


@pytest.mark.parametrize("lang,expected", [("en", "print the result as JSON"), ("tr", "sonucu metin yerine JSON")])
def test_doctor_help_and_errors_are_in_the_chosen_language(capsys, lang, expected):
    with pytest.raises(SystemExit) as e:
        doctor.main(["--help", "--lang", lang])
    out = capsys.readouterr().out
    assert e.value.code == 0 and expected in out and "cli_help_help" not in out
    with pytest.raises(SystemExit):
        doctor.main(["--only", "nope", "--lang", lang])
    err = capsys.readouterr().err
    assert ("unknown check id: nope" if lang == "en" else "bilinmeyen denetim adı: nope") in err


@pytest.mark.parametrize("lang,stopped", [
    ("en", "Refresh: 2 matches refreshed, 1 changed, 0 failed"),
    ("tr", "Yenileme: 2 maç yenilendi, 1 değişti, 0 başarısız"),
])
def test_cli_messages_are_translated(lang, stopped):
    i18n = I18nManager()
    i18n.set_language(lang)
    assert stopped in i18n.t("ssc_refresh_summary", refreshed=2, changed=1, failed=0)
    assert "12.5" in i18n.t("details_finished", ok=1, total=8, rate="12.5")


# --- yeni kurulum İngilizce başlar -------------------------------------------------------------


def test_env_example_does_not_pin_a_language():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    # Dil satırı yorumdadır; 2.x adı hiç geçmez
    assert re.findall(r"(?m)^SOFASCORE_DISPLAY__LANGUAGE=", text) == [] and "# SOFASCORE_DISPLAY__LANGUAGE=" in text
    assert not re.findall(r"(?m)^#? ?APP_LANGUAGE=", text)
    # .env.example'ı kopyalayan kurulum: dil sistemi izler, sistem desteklenmiyorsa İngilizce
    for environ, expected in (({}, "en"), ({"LANG": "de_DE.UTF-8"}, "en"), ({"LANG": "tr_TR.UTF-8"}, "tr")):
        root = REPO  # yalnızca yol; .env SOFASCORE_ENV_FILE ile verilir
        ctx = doctor.Context(root=root, environ={**environ, "SOFASCORE_ENV_FILE": str(REPO / ".env.example")}, platform="linux")
        assert ctx.lang == expected


def test_shipped_defaults_do_not_pin_turkish():
    compose = [ln for ln in (REPO / "docker-compose.yml").read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in compose if "SOFASCORE_DISPLAY__LANGUAGE" in ln or "APP_LANGUAGE" in ln]
    assert '<html lang="en">' in (REPO / "frontend" / "index.html").read_text(encoding="utf-8")
    assert language.DEFAULT_LANGUAGE == "en"
    frontend = (REPO / "frontend" / "src" / "i18n.ts").read_text(encoding="utf-8")
    assert "DEFAULT_LANG: Lang = 'en'" in frontend and "fallbackLocale: 'en'" in frontend


def test_settings_api_says_whether_the_language_is_pinned(monkeypatch):
    """Web arayüzü ilk ziyarette yalnızca açıkça ayarlanmış dili izler; yoksa tarayıcının diline bakar."""
    from sofascore_scraper.web.app import app

    client = TestClient(app)
    for key in language.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(language, "_windows_ui_language", lambda: None)

    def shown() -> tuple:
        rows = client.get("/api/v1/settings").json()["data"]["settings"]
        row = next(row for row in rows if row["key"] == "display.language")
        return row["value"], row["source"] != "default"

    assert shown() == ("en", False)

    monkeypatch.setenv("LANG", "tr_TR.UTF-8")  # sunucunun sistem dili: CLI için geçerli, ayar değil
    assert shown() == ("tr", False)

    monkeypatch.setenv("SOFASCORE_DISPLAY__LANGUAGE", "tr")
    assert shown() == ("tr", True)


# --- yapılandırma katmanları: sofascore.toml ve Ayarlar sayfası (FX-22) ------------------------------------
# Uygulama dili ayar yükleyicisinin katman sırasıyla da bulur: ortam (uygulama `.env`'i ortama yükler) >
# sofascore.toml > overrides.json (Ayarlar sayfası) > sistem dili. Başlatıcı ve başlatma betikleri yükleyiciyi
# (dotenv'e bağlı) yüklemeden aynı sonucu doctor.Context'ten alır.

# (süreç ortamı, .env, sofascore.toml'un [display] language'ı, overrides.json'ınki, beklenen)
LAYER_CASES = [
    ({"LANG": "en_US.UTF-8"}, None, None, "tr", "tr"),  # Ayarlar sayfası sistem dilinin önünde
    ({"LANG": "tr_TR.UTF-8"}, None, "en", None, "en"),  # yapılandırma dosyası da
    ({"LANG": "en_US.UTF-8"}, None, "tr", "en", "tr"),  # dosya overrides'ın üstünde
    ({"LANG": "en_US.UTF-8"}, "SOFASCORE_DISPLAY__LANGUAGE=en\n", None, "tr", "en"),  # .env (ortam) overrides'ın üstünde
    ({"LANG": "en_US.UTF-8"}, "APP_LANGUAGE=en\n", None, "tr", "tr"),  # 2.x adı okunmaz
    ({"LANG": "en_US.UTF-8", "SOFASCORE_DISPLAY__LANGUAGE": "en"}, None, "tr", "tr", "en"),  # süreç ortamı hepsinin üstünde
    ({"LANG": "en_US.UTF-8", "SOFASCORE_DISPLAY__LANGUAGE": "tr"}, None, "en", "en", "tr"),
    ({"LANG": "en_US.UTF-8", "SOFASCORE_DISPLAY__LANGUAGE": "tr"}, "SOFASCORE_DISPLAY__LANGUAGE=tr\n", None, "en", "tr"),  # .env'den gelen de ortamdır
    ({"LANG": "tr_TR.UTF-8"}, None, None, None, "tr"),
    ({"LANG": "tr_TR.UTF-8", "SOFASCORE_CONFIG": "none"}, None, "en", None, "tr"),  # dosya araması kapalı
]


def _layers(tmp_path: Path, env_text, toml_language, overrides_language) -> None:
    (tmp_path / "config").mkdir(exist_ok=True)
    if env_text is not None:
        (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    if toml_language is not None:
        (tmp_path / "sofascore.toml").write_text(f'schema = 1\n[display]\nlanguage = "{toml_language}"\n',
                                                 encoding="utf-8")
    if overrides_language is not None:
        (tmp_path / "config" / "overrides.json").write_text(
            json.dumps({"display": {"language": overrides_language}}), encoding="utf-8")


@pytest.mark.parametrize("environ,env_text,toml_language,overrides_language,expected", LAYER_CASES)
def test_the_launcher_language_follows_the_config_layers(tmp_path, environ, env_text, toml_language,
                                                         overrides_language, expected):
    _layers(tmp_path, env_text, toml_language, overrides_language)
    ctx = doctor.Context(root=tmp_path, environ=environ, platform="linux")
    assert ctx.lang == expected

    # Yükleyici aynı dili verir (python-dotenv `.env`'i süreç ortamına yükler; süreç ortamı önce gelir)
    from sofascore_scraper.config import loader

    dotenv = dict(ctx.file_env)
    use_file = toml_language is not None and environ.get("SOFASCORE_CONFIG") != "none"
    loaded = loader.load_settings(
        config_file=tmp_path / "sofascore.toml" if use_file else None,
        environ={**dotenv, **environ}, dotenv_values=dotenv, overrides_file=tmp_path / "config" / "overrides.json")
    configured = loaded.source(loader.LANGUAGE_KEY).layer != loader.LAYER_DEFAULT
    found = loaded.settings.display.language if configured else language.resolve_language(environ, "linux")
    assert found == expected


def test_an_unreadable_config_file_does_not_break_the_launcher_language(tmp_path):
    (tmp_path / "sofascore.toml").write_text("[display\nlanguage = ", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "overrides.json").write_text("{not json", encoding="utf-8")
    assert doctor.Context(root=tmp_path, environ={"LANG": "tr_TR.UTF-8"}, platform="linux").lang == "tr"


def _app_lang_function(script: str) -> str:
    text = (REPO / script).read_text(encoding="utf-8")
    match = re.search(r"^app_lang\(\) \{\n.*?^\}\n", text, flags=re.S | re.M)
    assert match, f"{script} has no app_lang()"
    return match.group(0)


@pytest.mark.skipif(os.name == "nt", reason="bash betikleri POSIX'te çalışır")
def test_the_shell_launchers_ask_the_app_for_the_language(tmp_path):
    """Python varken başlatma betikleri dili uygulamaya sorar: Ayarlar sayfasında seçilen dil de sayılır."""
    bash = shutil.which("bash")
    if bash is None or shutil.which("python3") is None:
        pytest.skip("bash ya da python3 yok")
    scripts = ("start-sofascore.sh", "Start SofaScore.command")
    assert len({_app_lang_function(script) for script in scripts}) == 1
    for name in scripts:
        text = (REPO / name).read_text(encoding="utf-8")
        assert 'case "$(app_lang)" in tr) UI_LANG=tr ;; en) UI_LANG=en ;; esac' in text
    config = tmp_path / "config"
    config.mkdir()
    (config / "overrides.json").write_text(json.dumps({"display": {"language": "tr"}}), encoding="utf-8")
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "en_US.UTF-8", "SOFASCORE_CONFIG": "none",
           "SOFASCORE_CONFIG_DIR": str(config), "SOFASCORE_ENV_FILE": str(tmp_path / "none.env")}
    script = "set -uo pipefail\n" + _app_lang_function(scripts[0]) + "app_lang\n"
    run = subprocess.run([bash, "-c", script], cwd=str(REPO), env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and run.stdout.strip() == "tr", run.stderr
    # Python cevap veremezse (sofascore_scraper yok) çıktı boştur ve betik kendi tahminini kullanır
    run = subprocess.run([bash, "-c", script], cwd=str(tmp_path), env=env, capture_output=True, text=True,
                         timeout=60)
    assert run.returncode == 0 and run.stdout.strip() == ""
