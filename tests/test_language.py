"""
Dil kuralı: açık ayar (APP_LANGUAGE) > sistem dili > İngilizce.

Kural src/language.py'de durur; kurulum ve başlatma betikleri (bash, PowerShell) Python daha
yokken çalıştıkları için aynı kuralı kendileri uygular. Aynı örnek tablosu hepsine sorulur.
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

from src import doctor, language
from src.i18n import I18nManager, app_language

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
    ({"LANG": "tr_TR.UTF-8", "APP_LANGUAGE": "en"}, None, "en"),
    ({"LANG": "en_US.UTF-8", "APP_LANGUAGE": "tr"}, None, "tr"),
    ({"LANG": "en_US.UTF-8", "APP_LANGUAGE": "TR"}, None, "tr"),
    ({"LANG": "tr_TR.UTF-8", "APP_LANGUAGE": "de"}, None, "tr"),  # desteklenmeyen ayar yok sayılır
    # Eski LANGUAGE yalnızca tam olarak tr / en ise ayardır; gettext listesi ("tr_TR:tr") değildir
    ({"LANG": "en_US.UTF-8", "LANGUAGE": "tr"}, None, "tr"),
    ({"LANG": "en_US.UTF-8", "LANGUAGE": "tr_TR:tr"}, None, "en"),
    # .env'deki ayar
    ({"LANG": "en_US.UTF-8"}, "MAX_CONCURRENT=5\nAPP_LANGUAGE=tr\n", "tr"),
    ({"LANG": "tr_TR.UTF-8"}, 'APP_LANGUAGE="en"\n', "en"),
    ({"LANG": "tr_TR.UTF-8"}, "APP_LANGUAGE=\n", "tr"),  # boş = ayarlanmamış (.env.example böyle gelir)
    ({"LANG": "en_US.UTF-8", "APP_LANGUAGE": "en"}, "APP_LANGUAGE=tr\n", "en"),  # süreç ortamı .env'in önünde
]
_IDS = [f"{i}-{expected}" for i, (_env, _file, expected) in enumerate(CASES)]


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
    assert language.explicit_language({"APP_LANGUAGE": "tr"}) == "tr"
    assert language.explicit_language({"APP_LANGUAGE": ""}) is None
    assert language.detected_language({"APP_LANGUAGE": "en", "LANG": "tr_TR.UTF-8"}, platform="linux") == "tr"
    assert language.detected_language({}, platform="linux") is None


def test_windows_falls_back_to_the_user_interface_language(monkeypatch):
    ui = {"value": "tr_TR"}
    monkeypatch.setattr(language, "_windows_ui_language", lambda: ui["value"])
    assert language.resolve_language({}, platform="win32") == "tr"
    assert language.resolve_language({"APP_LANGUAGE": "en"}, platform="win32") == "en"
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
    monkeypatch.setenv("APP_LANGUAGE", "en")
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
@pytest.mark.parametrize("environ,env_text,expected", CASES, ids=_IDS)
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
    env = {k: v for k, v in os.environ.items() if k not in language.ENV_KEYS}
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
    "environ,env_text,expected", [c for c in CASES if c[0]], ids=[f"{i}-{c[2]}" for i, c in enumerate(CASES) if c[0]]
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
    for name in ("main.py", "src/match_data_fetcher.py", "src/bridge_health.py", "src/match_fetcher.py", "src/web/fetch_job.py"):
        used |= set(re.findall(r"""\bt\(\s*['"]([a-z0-9_]+)['"]""", (REPO / name).read_text(encoding="utf-8")))
    launcher = (REPO / "scripts" / "start_web.py").read_text(encoding="utf-8")
    used |= {"launcher_" + key for key in re.findall(r'_t\(\s*"([a-z_]+)"', launcher)}
    assert len(used) > 60
    assert used <= set(en), sorted(used - set(en))
    # Kullanılmayan çeviri kalmasın (bu değişiklikle eklenen ön ekler için)
    main_text = (REPO / "main.py").read_text(encoding="utf-8")
    fetcher_text = (REPO / "src" / "match_data_fetcher.py").read_text(encoding="utf-8")
    doctor_text = (REPO / "src" / "doctor.py").read_text(encoding="utf-8")
    for key in en:
        if key.startswith("launcher_"):
            assert f'"{key[len("launcher_"):]}"' in launcher, key
        elif key.startswith("cli_"):
            assert f'"{key}"' in main_text or f'"{key}"' in doctor_text, key
        elif key.startswith("details_"):
            assert f'"{key}"' in fetcher_text, key


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_help_texts_survive_argparse_formatting(lang):
    """argparse yardım metinlerini %-biçimlendirir: yalın bir "%" --help'i çökertir."""
    for key, text in _locale(lang).items():
        if key.startswith(("cli_help_", "cli_description", "cli_epilog", "doctor_cli_")):
            assert text.replace("{ids}", "").replace("{valid}", "") % {"prog": "main.py"}, key
            assert not re.search(r"\{(?!ids\}|valid\})", text), key  # str.format: süslü ayraç yok


@pytest.mark.parametrize("lang,expected,absent", [
    ("en", ["Start the web interface", "Headless / CI examples", "Show this help message", "--diagnostics [PATH]"], "arayüz"),
    ("tr", ["Web arayüzünü başlatır", "Headless / CI örnekleri", "Bu yardım iletisini", "--diagnostics [YOL]"], "Start the"),
])
def test_main_help_is_in_the_chosen_language(monkeypatch, capsys, lang, expected, absent):
    import main as cli

    i18n = I18nManager()
    i18n.set_language(lang)
    monkeypatch.setattr(cli, "get_i18n", lambda: i18n)
    monkeypatch.setattr("sys.argv", ["main.py", "--help"])
    with pytest.raises(SystemExit) as e:
        cli.parse_arguments()
    out = capsys.readouterr().out
    assert e.value.code == 0
    for text in expected:
        assert text in out, text
    assert absent not in out and "cli_" not in out  # çevrilmemiş anahtar görünmüyor


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


@pytest.mark.parametrize("lang,usage,stopped", [
    ("en", "--watch needs --sport", "Refresh: 2 matches refreshed, 1 changed, 0 failed"),
    ("tr", "--watch için --sport", "Yenileme: 2 maç yenilendi, 1 değişti, 0 başarısız"),
])
def test_cli_messages_are_translated(lang, usage, stopped):
    i18n = I18nManager()
    i18n.set_language(lang)
    assert usage in i18n.t("cli_watch_usage")
    assert stopped in i18n.t("cli_refresh_summary", refreshed=2, changed=1, failed=0)
    assert "--update-all" in i18n.t("cli_headless_usage")
    assert "12.5" in i18n.t("details_finished", ok=1, total=8, rate="12.5")


def test_watch_without_arguments_explains_itself_in_the_app_language(monkeypatch, capsys):
    import main as cli

    monkeypatch.setenv("DATA_DIR", os.environ["DATA_DIR"])  # main --data-dir ortamı değiştirebilir
    monkeypatch.setattr("sys.argv", ["main.py", "--watch"])
    for lang, expected in (("en", "--watch needs --sport"), ("tr", "--watch için --sport")):
        i18n = I18nManager()
        i18n.set_language(lang)
        monkeypatch.setattr(cli, "get_i18n", lambda i18n=i18n: i18n)
        assert cli.main() == 2
        assert expected in capsys.readouterr().err


# --- yeni kurulum İngilizce başlar -------------------------------------------------------------


def test_env_example_does_not_pin_a_language():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    assert re.findall(r"(?m)^APP_LANGUAGE=(.*)$", text) == [""]
    # .env.example'ı kopyalayan kurulum: dil sistemi izler, sistem desteklenmiyorsa İngilizce
    for environ, expected in (({}, "en"), ({"LANG": "de_DE.UTF-8"}, "en"), ({"LANG": "tr_TR.UTF-8"}, "tr")):
        root = REPO  # yalnızca yol; .env SOFASCORE_ENV_FILE ile verilir
        ctx = doctor.Context(root=root, environ={**environ, "SOFASCORE_ENV_FILE": str(REPO / ".env.example")}, platform="linux")
        assert ctx.lang == expected


def test_shipped_defaults_do_not_pin_turkish():
    compose = [ln for ln in (REPO / "docker-compose.yml").read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in compose if "APP_LANGUAGE" in ln]
    assert '<html lang="en">' in (REPO / "frontend" / "index.html").read_text(encoding="utf-8")
    assert language.DEFAULT_LANGUAGE == "en"
    frontend = (REPO / "frontend" / "src" / "i18n.ts").read_text(encoding="utf-8")
    assert "DEFAULT_LANG: Lang = 'en'" in frontend and "fallbackLocale: 'en'" in frontend


def test_settings_api_says_whether_the_language_is_pinned(monkeypatch):
    """Web arayüzü ilk ziyarette yalnızca açıkça ayarlanmış dili izler; yoksa tarayıcının diline bakar."""
    from src.web.app import app

    client = TestClient(app)
    for key in language.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(language, "_windows_ui_language", lambda: None)
    body = client.get("/api/settings").json()
    assert (body["language"], body["language_explicit"]) == ("en", False)

    monkeypatch.setenv("LANG", "tr_TR.UTF-8")  # sunucunun sistem dili: CLI için geçerli, ayar değil
    body = client.get("/api/settings").json()
    assert (body["language"], body["language_explicit"]) == ("tr", False)

    monkeypatch.setenv("APP_LANGUAGE", "tr")
    body = client.get("/api/settings").json()
    assert (body["language"], body["language_explicit"]) == ("tr", True)
