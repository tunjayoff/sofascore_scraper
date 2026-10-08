"""Son log satırları, tanılama paketi ve bunların uç noktaları / CLI bayrağı."""
from __future__ import annotations

import io
import json
import logging
import os
import socket
import sqlite3
import subprocess
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

from sofascore_scraper import bridge_health, diagnostics, redact
from sofascore_scraper import logger as app_logger
from sofascore_scraper.store import LeaseHeld
from sofascore_scraper.store.jobs import JOB_COLUMNS
from sofascore_scraper.version import __version__
from sofascore_scraper.web.app import app
from sofascore_scraper.web.jobs import JobStore, default_db_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3OTAwMDAwMDAsInN1YiI6InNvZmEifQ.c2lnbmF0dXJlLXZhbHVlLTEyMw"
PROXY = "http://scraper:Pr0xy-P4ss!word@proxy.example.com:8080"
API_KEY = "sk-live-0123456789abcdef"
PLAIN_UNKNOWN = "an-unknown-setting-value"
SECRETS = ("Pr0xy-P4ss!word", "scraper:", JWT, "eyJhbGci", API_KEY, "abc123def", PLAIN_UNKNOWN)

client = TestClient(app)


@pytest.fixture
def log_dir(tmp_path):
    """Kök logger'ı geçici bir log dizinine yönlendirir (INFO), sonunda eski haline döndürür."""
    keys = ("LOG_DIR", "LOG_TO_FILE", "LOG_MAX_MB", "LOG_BACKUP_COUNT", "LOG_LEVEL", "DEBUG")
    saved = {k: os.environ.get(k) for k in keys}
    saved_level = logging.getLogger().level

    def _apply(**env):
        os.environ["LOG_DIR"] = str(tmp_path / "logs")
        os.environ["LOG_LEVEL"] = "DEBUG"
        os.environ.pop("DEBUG", None)
        for key, value in env.items():
            os.environ[key] = str(value)
        app_logger.setup_logger(force=True)
        return tmp_path / "logs"

    _apply()
    yield _apply
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    app_logger.setup_logger(force=True)
    logging.getLogger().setLevel(saved_level)


@pytest.fixture
def secrets_everywhere(tmp_path, monkeypatch):
    """Gizli değerler ortamda ve .env'de; ayrıca uygulamanın tanımadığı bir anahtar."""
    env = tmp_path / ".env"
    env.write_text(
        f"MAX_CONCURRENT=7\nTHIRD_PARTY_API_KEY={API_KEY}\nSOME_OTHER_SETTING={PLAIN_UNKNOWN}\nEMPTY_ONE=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(env))
    monkeypatch.setenv("SOFA_CAPTCHA_TOKEN", JWT)
    monkeypatch.setenv("USE_PROXY", "true")
    monkeypatch.setenv("PROXY_URL", PROXY)
    monkeypatch.setenv("MAX_CONCURRENT", "7")
    redact.refresh()
    yield
    monkeypatch.undo()
    redact.refresh()


def _log_some(log_name="WebAPI"):
    log = logging.getLogger(log_name)
    log.debug("ayrıntı satırı")
    log.info("bilgi satırı")
    log.warning("uyarı satırı")
    try:
        raise RuntimeError("patladı")
    except RuntimeError:
        log.exception("hata satırı")
    return log


def _unzip(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


# --- son log satırları -------------------------------------------------------------------

def test_entries_are_parsed_oldest_first_with_tracebacks_attached(log_dir):
    _log_some()
    out = diagnostics.read_log_entries(limit=50)
    assert out["enabled"] is True
    assert out["file"] == app_logger.log_file_path()
    assert out["level"] == "DEBUG"
    messages = [e["message"] for e in out["entries"]]
    levels = [e["level"] for e in out["entries"]]
    assert levels[-4:] == ["DEBUG", "INFO", "WARNING", "ERROR"]
    assert messages[-4:-1] == ["ayrıntı satırı", "bilgi satırı", "uyarı satırı"]
    last = out["entries"][-1]
    assert last["message"].startswith("hata satırı\nTraceback (most recent call last)")
    assert last["message"].rstrip().endswith("RuntimeError: patladı")
    assert last["logger"] == "WebAPI" and last["pid"] == os.getpid()
    assert out["count"] == len(out["entries"])


@pytest.mark.parametrize(
    "level, expected",
    [("DEBUG", ["DEBUG", "INFO", "WARNING", "ERROR"]), ("WARNING", ["WARNING", "ERROR"]), ("error", ["ERROR"]),
     ("CRITICAL", [])],
)
def test_level_filter_is_a_minimum(log_dir, level, expected):
    _log_some("FilterTest")
    out = diagnostics.read_log_entries(limit=50, min_level=level)
    assert [e["level"] for e in out["entries"] if e["logger"] == "FilterTest"] == expected
    assert out["min_level"] == level.upper()


def test_unknown_level_is_rejected(log_dir):
    with pytest.raises(ValueError):
        diagnostics.read_log_entries(min_level="LOUD")


def test_limit_keeps_the_newest_and_is_bounded(log_dir):
    log = logging.getLogger("WebAPI")
    for i in range(30):
        log.info("satır %02d", i)
    out = diagnostics.read_log_entries(limit=5)
    assert [e["message"] for e in out["entries"]] == [f"satır {i:02d}" for i in range(25, 30)]
    # Sınırın üstü istenirse üst sınıra, altı istenirse 1'e çekilir
    assert len(diagnostics.read_log_entries(limit=10**9)["entries"]) <= diagnostics.MAX_LOG_ENTRIES
    assert len(diagnostics.read_log_entries(limit=-3)["entries"]) == 1


def test_older_rotated_files_are_read_when_needed(log_dir):
    log_dir(LOG_MAX_MB="0.002", LOG_BACKUP_COUNT="3")
    log = logging.getLogger("WebAPI")
    for i in range(60):
        log.info("dönen %02d %s", i, "x" * 40)
    assert len(app_logger.log_files()) > 1
    out = diagnostics.read_log_entries(limit=40)
    numbers = [int(e["message"].split()[1]) for e in out["entries"]]
    assert numbers == list(range(20, 60))  # dosya sınırını aşar, sıra bozulmaz


def test_scan_is_bounded_by_bytes(log_dir, monkeypatch):
    log = logging.getLogger("WebAPI")
    for i in range(200):
        log.info("uzun %03d %s", i, "x" * 100)
    monkeypatch.setattr(diagnostics, "MAX_SCAN_BYTES", 2000)
    out = diagnostics.read_log_entries(limit=2000)
    assert 0 < len(out["entries"]) < 20
    assert out["entries"][-1]["message"].startswith("uzun 199")
    assert len(diagnostics.log_tail_text(5000).encode("utf-8")) <= 2000


def test_only_as_much_of_the_file_as_needed_is_read(log_dir, monkeypatch):
    # Son birkaç kayıt için dosyanın tamamı okunmaz; yetmezse pencere büyür
    log = logging.getLogger("WebAPI")
    for i in range(300):
        log.info("pencere %03d %s", i, "x" * 100)
    monkeypatch.setattr(diagnostics, "_FIRST_WINDOW_BYTES", 1000)
    sizes = []
    real = diagnostics._read_tail

    def spy(path, max_bytes):
        sizes.append(max_bytes)
        return real(path, max_bytes)

    monkeypatch.setattr(diagnostics, "_read_tail", spy)
    few = diagnostics.read_log_entries(limit=3)
    assert [e["message"][:11] for e in few["entries"]] == ["pencere 297", "pencere 298", "pencere 299"]
    assert sizes == [1000]

    sizes.clear()
    many = diagnostics.read_log_entries(limit=50)
    assert [int(e["message"].split()[1]) for e in many["entries"]] == list(range(250, 300))
    assert sizes == [1000, 4000, 16000]
    assert os.path.getsize(app_logger.log_file_path()) > 2 * 16000


def test_secrets_already_in_the_file_are_masked_on_read(log_dir, secrets_everywhere):
    # Maskeleme eklenmeden önce yazılmış (ya da başka bir araçla eklenmiş) satırlar
    path = log_dir() / app_logger.LOG_FILE_NAME
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"2026-10-01 10:00:00,000 ERROR    [1] Utils: Proxy/Bağlantı hatası: {PROXY}\n")
        f.write(f"2026-10-01 10:00:01,000 INFO     [1] ChallengeSolver: Cookie: sofa_captcha=abc123def; t={JWT}\n")
    entries = diagnostics.read_log_entries(limit=50)["entries"]
    text = json.dumps(entries, ensure_ascii=False) + diagnostics.log_tail_text(100)
    assert "Proxy/Bağlantı hatası" in text
    for secret in SECRETS:
        assert secret not in text, secret


def test_no_log_file_means_empty_result_not_an_error(log_dir):
    log_dir(LOG_TO_FILE="false")
    out = diagnostics.read_log_entries()
    assert out == {"enabled": False, "file": None, "level": "DEBUG", "min_level": None, "count": 0, "entries": []}
    assert diagnostics.log_tail_text() == ""
    assert "log_tail.txt" in _unzip(diagnostics.build_bundle())


# --- tanılama özeti ----------------------------------------------------------------------

def test_summary_has_the_expected_sections(log_dir):
    doc = diagnostics.collect(source="cli")
    assert doc["source"] == "cli"
    assert doc["app"]["name"] == "sofascore-scraper"
    assert doc["app"]["version"] == __version__  # pyproject.toml (sofascore_scraper/version.py)
    assert set(doc["app"]) == {"name", "version", "commit", "ref"}
    assert doc["runtime"]["python"] == ".".join(map(str, sys.version_info[:3]))
    assert doc["runtime"]["platform"] and doc["runtime"]["system"]
    assert doc["runtime"]["packages"]["fastapi"]
    assert doc["bridge"]["state"] == bridge_health.OK
    assert set(doc["throttle"]) >= {"enabled", "requests_per_second", "shared"}
    assert doc["logging"]["level"] == "DEBUG" and doc["logging"]["to_file"] is True
    assert doc["leagues"]["configured"] == 1
    assert doc["data_dir"]["exists"] is True
    assert "recent" in doc["jobs"]
    json.dumps(doc)  # JSON'a çevrilebilir


def test_setup_check_is_included_without_starting_a_browser(log_dir, monkeypatch):
    from sofascore_scraper import doctor

    def no_probe(*args, **kwargs):
        raise AssertionError("the diagnostics summary must not start the browser probe")

    monkeypatch.setattr(doctor, "probe_browser", no_probe)
    section = diagnostics.collect()["doctor"]
    ids = [check["id"] for check in section["checks"]]
    assert "browser" not in ids and section["skipped"] == ["browser"]
    assert {"python", "packages", "data_dir", "config_dir", "env"} <= set(ids)
    assert section["status"] in (doctor.OK, doctor.WARN, doctor.FAIL)
    assert section["counts"][doctor.OK] >= 1
    assert all(check["code"] and check["summary"] for check in section["checks"])


def test_setup_check_problems_reach_the_summary_masked(log_dir, secrets_everywhere, monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT", "çok")  # geçersiz sayı: .env denetimi hata verir
    # Ayrıştırılamayan satır ('=' yok): doctor terminalde metnini gösterir, pakete metni girmez
    with open(os.environ["SOFASCORE_ENV_FILE"], "a", encoding="utf-8") as f:
        f.write("VENDOR_LICENSE unparsed-line-secret-value\n")
    doc = diagnostics.collect()
    env = next(check for check in doc["doctor"]["checks"] if check["id"] == "env")
    assert env["status"] == "fail" and env["code"] == "env_invalid"
    assert {p["setting"] for p in env["detail"]["problems"]} == {"MAX_CONCURRENT", "line 5"}
    assert doc["doctor"]["ok"] is False
    flat = json.dumps(doc, ensure_ascii=False)
    for secret in SECRETS + ("unparsed-line-secret-value", "VENDOR_LICENSE"):
        assert secret not in flat, secret


def test_bridge_health_is_reported(log_dir):
    for _ in range(4):
        bridge_health.record_failure(bridge_health.KIND_CHALLENGE, "çözülemedi")
    doc = diagnostics.collect()
    assert doc["bridge"]["state"] == bridge_health.DEGRADED
    assert doc["bridge"]["consecutive_failures"] == 4
    assert doc["bridge"]["last_error"]["kind"] == bridge_health.KIND_CHALLENGE


def test_settings_are_reported_with_secrets_masked(log_dir, secrets_everywhere, monkeypatch):
    monkeypatch.delenv("WAIT_TIME_MIN", raising=False)
    doc = diagnostics.collect()
    values = doc["settings"]["values"]
    assert values["MAX_CONCURRENT"] == "7"
    assert values["USE_PROXY"] == "true"
    assert values["SOFA_CAPTCHA_TOKEN"] == "***"
    assert values["PROXY_URL"] == "http://***@proxy.example.com:8080"
    assert values["WAIT_TIME_MIN"] is None  # ayarlanmamış: varsayılan
    # Uygulamanın tanımadığı anahtarlar: yalnızca adları
    env_file = doc["settings"]["env_file"]
    assert env_file["other_keys_set"] == ["SOME_OTHER_SETTING", "THIRD_PARTY_API_KEY"]
    assert env_file["other_keys_empty"] == ["EMPTY_ONE"]
    flat = json.dumps(doc, ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in flat, secret


def test_home_directory_is_not_exposed(log_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    doc = diagnostics.collect()
    assert doc["logging"]["file"].startswith("~")
    assert str(tmp_path) not in json.dumps(doc, ensure_ascii=False)
    logging.getLogger("WebAPI").info("dosya: %s", tmp_path / "data" / "x.json")
    tail = diagnostics.log_tail_text(50)
    assert str(tmp_path) not in tail and "~" in tail


def test_bundle_masks_the_value_of_an_unknown_sink_option_in_the_environment(log_dir, monkeypatch):
    # Paket bütün SOFASCORE_ değişkenlerini yazar. SOFASCORE_SINKS'te sink'lerin tanımadığı bir anahtar
    # (yanlış yazılmış `url`, uydurulmuş bir anahtar, başka türün anahtarı) `ssc config show`daki gibi `***` olur.
    # Değerler sahtedir ve çalışırken parçalardan kurulur.
    path_part, signing = "-".join(("fake", "path", "part")), "-".join(("fake", "signing", "value"))
    address = f"https://hooks.example.org/services/T000/B000/{path_part}"
    monkeypatch.setenv("SOFASCORE_SINKS", json.dumps([
        {"name": "ops", "type": "webhook", "url": address, "allow_unsigned": True, "batch_size": 50,
         "webhook_url": address, "note": {"signing": signing}, "keep": 3},
        {"name": "feed", "type": "file", "path": "out/live.ndjson", "keep": 7, "sports": ["football"], "batch_size": 5},
    ]))
    files = _unzip(diagnostics.build_bundle(source="cli"))
    values = json.loads(files["diagnostics.json"])["settings"]["values"]
    assert json.loads(values["SOFASCORE_SINKS"]) == [
        {"name": "ops", "type": "webhook", "url": "https://hooks.example.org/***", "allow_unsigned": True,
         "batch_size": 50, "webhook_url": "***", "note": "***", "keep": "***"},
        {"name": "feed", "type": "file", "path": "out/live.ndjson", "keep": 7, "sports": ["football"],
         "batch_size": "***"},
    ]
    for private in (path_part, signing, "/services/"):
        assert private not in files["diagnostics.json"], private
    # Tablo listesi olmayan değer, eskisi gibi, tümüyle maskelenir
    monkeypatch.setenv("SOFASCORE_SINKS", json.dumps([address]))
    assert diagnostics._settings()["values"]["SOFASCORE_SINKS"] == "***"
    monkeypatch.setenv("SOFASCORE_SINKS", f"not json {address}")
    assert diagnostics._settings()["values"]["SOFASCORE_SINKS"] == "***"


# --- son iş ------------------------------------------------------------------------------

def test_last_job_summary_comes_from_the_job_store(log_dir, monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    store = JobStore(default_db_path(str(data_dir)))
    store.create_running({"mode": "full", "selections": [{"league_id": 17}]})
    store.update(append_log=f"403 alındı, proxy {PROXY}", matches_total=10, matches_done=7, matches_failed=3)
    store.update(
        status="Failed",
        current_task="SofaScore engelliyor",
        result={"failed_count": 3, "failed": [{"match_id": i, "reason": "403"} for i in range(120)]},
        finished=True,
    )
    job = diagnostics.collect()["jobs"]["recent"][0]
    assert job["status"] == "failed"
    assert job["current_task"] == "SofaScore engelliyor"
    assert (job["matches_total"], job["matches_done"], job["matches_failed"]) == (10, 7, 3)
    assert job["payload"]["mode"] == "full"
    assert "Pr0xy-P4ss!word" not in json.dumps(job)
    assert "proxy.example.com" in job["log"][0]
    # Uzun listeler kırpılır: paket küçük kalır
    assert len(job["result"]["failed"]) == 51 and job["result"]["failed"][-1] == "... 70 more"


def test_collecting_does_not_touch_a_running_job(log_dir, monkeypatch, tmp_path):
    # CLI'dan paket üretmek, çalışan web sunucusunun işini "interrupted" yapmamalı
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    db = default_db_path(str(data_dir))
    JobStore(db).create_running({"mode": "details"})
    before = os.stat(db).st_mtime_ns

    job = diagnostics.collect(source="cli")["jobs"]["recent"][0]
    assert job["status"] == "running"

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "running"
    finally:
        conn.close()
    assert os.stat(db).st_mtime_ns == before


def test_missing_job_db_is_not_created(log_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "nowhere"))
    jobs = diagnostics.collect()["jobs"]
    assert jobs["exists"] is False and jobs["recent"] == []
    assert not (tmp_path / "nowhere").exists()


# --- son iş: seçilen sütunlar ve makine adı ----------------------------------------------

HOST = "build-box-17"


def _strings(value):
    """JSON'a benzer bir yapıdaki bütün metinler (sözlük anahtarları dahil)."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def test_job_history_leaves_out_the_host_name_secrets_and_home_paths(log_dir, monkeypatch, tmp_path):
    # Geçiş 0002'nin sütunlarıyla yazılmış bir iş: işi başlatan makinenin adı, belirtimde ve hata metninde
    # parolalı bir proxy adresi, ev dizininin altında yollar, hata ayrıntısında başka bir makinenin adı
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(socket, "gethostname", lambda: HOST)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    store = JobStore(default_db_path(str(data_dir)))
    store.create_running(
        {"mode": "full"},
        kind="fetch",
        origin={"face": "cli", "pid": 4242, "host": HOST},
        spec={"mode": "full", "proxy": PROXY, "target": str(tmp_path / "exports" / "out.csv")},
    )
    store.update(
        status="Failed",
        state="failed",
        current_task=f"Error: lock held (pid 77, makine {HOST})",
        append_log=f"[failed] the writer lease is held on {HOST}",
        error={
            "code": "job_running",
            "message": f"CONNECT tunnel failed via {PROXY} (pid 77, makine {HOST})",
            "details": {
                "path": str(data_dir / "x.json"),
                "holder": {"lease": "writer", "pid": 77, "host": "nas-01.lan"},
            },
        },
        finished=True,
    )

    job = diagnostics.collect(source="cli")["jobs"]["recent"][0]
    # Tanıya yarayanlar duruyor: tür, durum, zamanlar, işi başlatan yüz, hata kodu ve maskelenmiş iletisi
    assert (job["kind"], job["status"]) == ("fetch", "failed")
    assert job["created_at"] and job["started_at"] and job["finished_at"]
    assert isinstance(job["heartbeat_at"], int)
    assert job["origin"] == {"face": "cli", "pid": 4242, "same_host": True}
    assert job["spec"]["mode"] == "full"
    assert job["spec"]["proxy"] == "http://***@proxy.example.com:8080"
    assert job["spec"]["target"].startswith("~")
    assert job["error"]["code"] == "job_running"
    assert "proxy.example.com:8080" in job["error"]["message"] and "makine ***" in job["error"]["message"]
    assert job["error"]["details"]["holder"] == {"lease": "writer", "pid": 77, "host": "***"}
    assert job["error"]["details"]["path"].startswith("~")
    assert job["current_task"] == "Error: lock held (pid 77, makine ***)"
    assert job["log"] == ["[failed] the writer lease is held on ***"]
    # Sütunlar adıyla seçilir: ham JSON sütunları ve kilit sahibinin kimliği pakette yok
    assert not {"owner", "origin_json", "spec_json", "error_json"} & set(job)

    files = _unzip(diagnostics.build_bundle(source="cli"))
    texts = list(_strings(json.loads(files["diagnostics.json"]))) + [files["log_tail.txt"]]
    for leaked in (HOST, "nas-01", "Pr0xy-P4ss!word", "scraper:", str(tmp_path)):
        assert not [text for text in texts if leaked in text], leaked


@pytest.mark.parametrize("origin, expected", [
    ({"face": "api", "pid": 7, "host": "nas-01"}, {"face": "api", "pid": 7, "same_host": False}),
    ({"face": "scheduler", "pid": None, "host": None}, {"face": "scheduler", "pid": None, "same_host": None}),
    # Kayda sonradan eklenen bir alan pakete kendiliğinden girmez
    ({"face": "cli", "pid": 9, "host": HOST, "user": "someone"}, {"face": "cli", "pid": 9, "same_host": True}),
    (None, None),
])
def test_job_origin_keeps_the_face_and_the_pid_only(log_dir, monkeypatch, tmp_path, origin, expected):
    monkeypatch.setattr(socket, "gethostname", lambda: HOST)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    store = JobStore(default_db_path(str(data_dir)))
    store.create_running({"mode": "details"}, origin=origin)
    store.update(finished=True)
    doc = diagnostics.collect()
    assert doc["jobs"]["recent"][0]["origin"] == expected
    assert not [text for text in _strings(doc["jobs"]) if "nas-01" in text or "someone" in text]


def test_host_names_are_masked_only_where_they_stand_alone():
    hosts = diagnostics._host_names(["pc.lan", None, "", "10.0.0.5"])
    assert hosts == ["10.0.0.5", "pc.lan", "pc"]  # noktalı adın ilk parçası da; IP'nin ilk parçası değil
    masked = diagnostics._mask_hosts({"pc": ["3 upcoming on pc, PC-4242 and pc.lan; 10 left", 10]}, hosts)
    assert masked == {"pc": ["3 upcoming on ***, ***-4242 and ***; 10 left", 10]}
    assert diagnostics._mask_hosts("pc", []) == "pc"


# --- log kuyruğu: makine adı ---------------------------------------------------------------

OTHER_HOST = "nas-01.lan"       # bir işin hatasında adı geçen kilit sahibi
ORIGIN_HOST = "ci-runner-3"     # bir işi başlatan başka makine


def _log_a_refused_lease(holder_host):
    """Çağıranın LeaseHeld'i traceback'iyle log'a yazması; ileti sofascore_scraper/store/lease.py'nin kurduğu biçimdedir."""
    try:
        raise LeaseHeld(
            f"'writer' kilidi alınamadı: veri dizini 'writer' kilidiyle başka bir sahipte (pid 77, makine {holder_host})",
            name="writer", pid=77, host=holder_host,
        )
    except LeaseHeld:
        logging.getLogger("WebAPI").exception("sync could not start")


def test_log_tail_of_the_bundle_masks_this_machine_and_the_hosts_of_the_listed_jobs(log_dir, monkeypatch, tmp_path):
    # Ev dizininin adı makinenin adıyla aynı (kullanıcı adı = makine adı): yol yine `~` olur
    home = tmp_path / HOST
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(socket, "gethostname", lambda: HOST)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    store = JobStore(default_db_path(str(data_dir)))
    store.create_running({"mode": "full"}, kind="fetch", origin={"face": "cli", "pid": 4242, "host": ORIGIN_HOST})
    store.update(
        status="Failed",
        state="failed",
        error={
            "code": "job_running",
            "message": "the writer lease is held",
            "details": {"holder": {"lease": "writer", "pid": 77, "host": OTHER_HOST}},
        },
        finished=True,
    )
    log = logging.getLogger("WebAPI")
    log.warning("started on %s (pid 4242); 3 upcoming matches", HOST)
    log.info("handing over to %s and to %s", HOST.upper(), ORIGIN_HOST)
    log.info("export: %s", home / "exports" / "out.csv")
    _log_a_refused_lease(OTHER_HOST)
    log.info("a machine that no listed job names: unrelated-box")

    tail = _unzip(diagnostics.build_bundle(source="cli"))["log_tail.txt"]
    for name in (HOST, HOST.upper(), "nas-01", ORIGIN_HOST, str(home)):
        assert name not in tail, name
    # Satırların geri kalanı duruyor: sözcüklerin içi bozulmaz, traceback ve pid kalır
    assert "started on *** (pid 4242); 3 upcoming matches" in tail
    assert "handing over to *** and to ***" in tail
    assert "(pid 77, makine ***)" in tail and "LeaseHeld" in tail and "Traceback" in tail
    assert "export: ~" in tail
    # Genel bir makine adı dedektörü değildir: yalnızca bu makinenin ve listelenen işlerin adları
    assert "unrelated-box" in tail
    # diagnostics.json aynı adları taşımaz (#71) ve işin kendisi duruyor
    files = _unzip(diagnostics.build_bundle(source="cli"))
    doc = json.loads(files["diagnostics.json"])
    assert doc["jobs"]["recent"][0]["error"]["details"]["holder"]["host"] == "***"
    assert "log tail" in files["README.txt"] and "log satırlarında" in files["README.txt"]


def test_log_tail_masks_this_machine_without_a_job_history(log_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "gethostname", lambda: "pc.lan")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "nowhere"))
    logging.getLogger("WebAPI").info("3 upcoming on pc, PC-4242 and pc.lan; 10 left")
    assert "3 upcoming on ***, ***-4242 and ***; 10 left" in diagnostics.log_tail_text(50)
    assert not (tmp_path / "nowhere").exists()


def test_log_tail_masks_this_machine_when_the_job_history_cannot_be_read(log_dir, monkeypatch):
    def boom(*_args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(socket, "gethostname", lambda: HOST)
    monkeypatch.setattr(diagnostics, "_jobs", boom)
    logging.getLogger("WebAPI").info("running on %s", HOST)
    files = _unzip(diagnostics.build_bundle(source="cli"))
    assert "running on ***" in files["log_tail.txt"] and HOST not in files["log_tail.txt"]
    assert json.loads(files["diagnostics.json"])["jobs"] == {"error": "OperationalError: database is locked"}


@pytest.mark.parametrize("db_name, extra", [("jobs.db", ()), ("state.db", ("kind", "owner"))])
def test_job_history_without_the_job_manager_columns_is_still_read(log_dir, monkeypatch, tmp_path, db_name, extra):
    # 2.x'in jobs.db'si (18 sütun) ve geçiş 0002'den önce yazılmış bir state.db (+ kind, owner):
    # origin_json, spec_json, error_json, heartbeat_at ve created_at sütunları yok
    data_dir = tmp_path / "data"
    (data_dir / ".meta").mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    conn = sqlite3.connect(str(data_dir / ".meta" / db_name))
    try:
        conn.execute("CREATE TABLE jobs ({})".format(", ".join(JOB_COLUMNS + extra)))
        conn.execute(
            "INSERT INTO jobs (id, status, payload_json, log_json, started_at, matches_total) VALUES (?, ?, ?, ?, ?, ?)",
            ("old-1", "completed", '{"mode": "full"}', '["bitti"]', "2026-09-01T10:00:00+00:00", 12),
        )
        if extra:
            conn.execute("UPDATE jobs SET kind = 'fetch', owner = 'a1b2c3d4e5f6a7b8'")
        conn.commit()
    finally:
        conn.close()

    jobs = diagnostics.collect()["jobs"]
    assert jobs["exists"] is True and jobs["db"].endswith(db_name)
    job = jobs["recent"][0]
    assert (job["id"], job["status"], job["matches_total"]) == ("old-1", "completed", 12)
    assert job["payload"] == {"mode": "full"} and job["log"] == ["bitti"] and job["result"] is None
    assert job.get("kind") == ("fetch" if extra else None)
    # Dosyada olmayan sütunun anahtarı da yok; `owner` hiç seçilmez
    assert not {"owner", "origin", "spec", "error", "created_at", "heartbeat_at"} & set(job)
    doc = json.loads(_unzip(diagnostics.build_bundle())["diagnostics.json"])
    assert doc["jobs"]["recent"][0]["id"] == "old-1"


def test_a_column_added_to_the_jobs_table_later_does_not_reach_the_bundle(log_dir, monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    db = default_db_path(str(data_dir))
    store = JobStore(db)
    store.create_running({"mode": "details"})
    store.update(finished=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN operator_note TEXT")
        conn.execute("UPDATE jobs SET operator_note = 'a-future-column-value'")
        conn.commit()
    finally:
        conn.close()
    doc = diagnostics.collect()
    job = doc["jobs"]["recent"][0]
    assert job["status"] == "completed" and "operator_note" not in job
    assert "a-future-column-value" not in json.dumps(doc)


@pytest.mark.skipif(os.name == "nt", reason="sembolik bağ")
@pytest.mark.parametrize("other_host", [True, False])
def test_setup_check_does_not_carry_the_host_name_of_a_profile_lock(log_dir, monkeypatch, tmp_path, other_host):
    # Chromium'un profil kilidi "<makine adı>-<pid>" bağıdır; doctor bunu ayrıntıya (ve başka makineyse özete) yazar
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setenv("SOFASCORE_BROWSER_PROFILE", str(profile))
    host = "old-container" if other_host else socket.gethostname()
    os.symlink(f"{host}-4242", profile / "SingletonLock")
    check = next(c for c in diagnostics.collect()["doctor"]["checks"] if c["id"] == "profile")
    assert check["detail"]["lock"] == "***-4242" and check["detail"]["lock_host"] == "***"
    if other_host:
        assert check["code"] == "profile_locked_other_host" and "***-4242" in check["summary"]
        assert not [text for text in _strings(check) if "old-container" in text]
    else:
        assert check["code"] in ("profile_stale_lock", "profile_in_use") and check["detail"]["lock_pid"] == 4242


def test_a_failing_section_does_not_break_the_bundle(log_dir, monkeypatch):
    def boom():
        raise RuntimeError("bozuk bölüm")

    monkeypatch.setattr(diagnostics, "_jobs", boom)
    doc = diagnostics.collect()
    assert doc["jobs"] == {"error": "RuntimeError: bozuk bölüm"}
    assert doc["runtime"]["python"]


# --- paket -------------------------------------------------------------------------------

def test_bundle_contents(log_dir):
    _log_some()
    files = _unzip(diagnostics.build_bundle(source="cli", log_lines=200))
    assert sorted(files) == ["README.txt", "diagnostics.json", "log_tail.txt"]
    doc = json.loads(files["diagnostics.json"])
    assert doc["source"] == "cli" and doc["runtime"]["python"]
    assert "uyarı satırı" in files["log_tail.txt"]
    assert "RuntimeError: patladı" in files["log_tail.txt"]
    assert "***" in files["README.txt"]


def test_bundle_log_tail_is_bounded(log_dir):
    log = logging.getLogger("WebAPI")
    for i in range(50):
        log.info("kuyruk %02d", i)
    tail = _unzip(diagnostics.build_bundle(log_lines=10))["log_tail.txt"]
    assert tail.count("\n") == 10
    assert "kuyruk 49" in tail and "kuyruk 39" not in tail


def test_bundle_never_contains_secrets(log_dir, secrets_everywhere, monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    store = JobStore(default_db_path(str(data_dir)))
    store.create_running({"mode": "full", "proxy": PROXY, "token": JWT})
    store.update(append_log=f"Cookie: sofa_captcha=abc123def; proxy={PROXY}", finished=True)
    bridge_health.record_failure(bridge_health.KIND_FORBIDDEN, f"403 via {PROXY} with X-Captcha: {JWT}")

    log = logging.getLogger("Utils")
    log.error(f"Proxy/Bağlantı hatası: Failed to connect to {PROXY} - proxy: açık")
    log.info("istek başlıkları: %s", {"Cookie": "sofa_captcha=abc123def", "X-Captcha": JWT})
    log.warning(f"üçüncü taraf anahtarı {API_KEY} reddedildi")
    try:
        raise ConnectionError(f"CONNECT tunnel failed via {PROXY}")
    except ConnectionError:
        log.exception("İstek hatası")
    # Maskelenmeden dosyaya düşmüş bir satır da olsun
    with open(app_logger.log_file_path(), "a", encoding="utf-8") as f:
        f.write(f"2026-10-01 10:00:00,000 ERROR    [1] Utils: eski satır {PROXY} {JWT} {API_KEY}\n")

    data = diagnostics.build_bundle(log_lines=diagnostics.MAX_TAIL_LINES)
    files = _unzip(data)
    for name, text in files.items():
        for secret in SECRETS:
            assert secret not in text, (name, secret)
    # İçerik boşaltılmadı: satırlar duruyor, yalnız gizli parçalar yok
    assert "Proxy/Bağlantı hatası" in files["log_tail.txt"]
    assert "proxy.example.com:8080" in files["log_tail.txt"]
    assert "eski satır" in files["log_tail.txt"]
    doc = json.loads(files["diagnostics.json"])
    assert doc["bridge"]["last_error"]["kind"] == bridge_health.KIND_FORBIDDEN
    assert doc["jobs"]["recent"][0]["payload"]["mode"] == "full"
    # Ham log dosyasında da yok (dosyaya elle eklenen satır hariç)
    raw = open(app_logger.log_file_path(), encoding="utf-8").read().replace("eski satır", "\0").split("\0")[0]
    for secret in SECRETS:
        assert secret not in raw, secret


def test_write_bundle_default_and_explicit_paths(log_dir, tmp_path):
    default = diagnostics.write_bundle()
    assert os.path.dirname(default) == app_logger.log_dir()
    assert os.path.basename(default).startswith("sofascore-diagnostics-") and default.endswith(".zip")
    assert zipfile.is_zipfile(default)

    explicit = diagnostics.write_bundle(str(tmp_path / "out" / "report.zip"))
    assert explicit == str(tmp_path / "out" / "report.zip") and zipfile.is_zipfile(explicit)

    into_dir = diagnostics.write_bundle(str(tmp_path / "out"))
    assert os.path.dirname(into_dir) == str(tmp_path / "out")
    assert os.path.basename(into_dir).startswith("sofascore-diagnostics-")


# --- uç noktalar -------------------------------------------------------------------------

def test_logs_endpoint(log_dir):
    _log_some("EndpointTest")
    r = client.get("/api/v1/logs", params={"limit": 3, "level": "WARNING"})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["enabled"] is True and body["min_level"] == "WARNING"
    assert [e["level"] for e in body["entries"]][-2:] == ["WARNING", "ERROR"]
    assert all(e["level"] in ("WARNING", "ERROR", "CRITICAL") for e in body["entries"])
    assert len(body["entries"]) <= 3

    r = client.get("/api/v1/logs")
    assert r.status_code == 200
    assert any(e["message"] == "ayrıntı satırı" for e in r.json()["data"]["entries"])


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": -1}, {"limit": diagnostics.MAX_LOG_ENTRIES + 1}, {"limit": "abc"}, {"level": "LOUD"},
     {"level": "warning"}],
)
def test_logs_endpoint_rejects_out_of_range_input(log_dir, params):
    assert client.get("/api/v1/logs", params=params).status_code == 422


def test_log_endpoints_cannot_be_pointed_at_other_files(log_dir, tmp_path):
    secret_file = tmp_path / "private.txt"
    secret_file.write_text("2026-10-01 10:00:00,000 INFO     [1] X: ÖZEL-DOSYA-İÇERİĞİ\n", encoding="utf-8")
    logging.getLogger("WebAPI").info("gerçek log satırı")

    for params in (
        {"path": str(secret_file)}, {"file": str(secret_file)}, {"name": "../private.txt"},
        {"log_file": "/etc/passwd"},
    ):
        r = client.get("/api/v1/logs", params=params)
        assert r.status_code == 200
        assert "ÖZEL-DOSYA-İÇERİĞİ" not in r.text and "root:" not in r.text
        assert r.json()["data"]["file"] == app_logger.log_file_path()
        bundle = client.get("/api/v1/diagnostics/bundle", params=params)
        assert "ÖZEL-DOSYA-İÇERİĞİ" not in "".join(_unzip(bundle.content).values())

    # Yol parametresi alan bir rota yok
    for url in ("/api/v1/logs/private.txt", "/api/v1/logs/..%2F..%2Fetc%2Fpasswd", "/api/v1/diagnostics/bundle/x",
                "/api/v1/diagnostics/..%2F..%2F.env"):
        assert client.get(url).status_code == 404, url


def test_diagnostics_endpoint(log_dir, secrets_everywhere):
    r = client.get("/api/v1/diagnostics")
    assert r.status_code == 200
    doc = r.json()["data"]
    assert doc["source"] == "web"
    assert doc["settings"]["values"]["SOFA_CAPTCHA_TOKEN"] == "***"
    for secret in SECRETS:
        assert secret not in r.text, secret


def test_bundle_endpoint_is_a_download(log_dir):
    logging.getLogger("WebAPI").warning("pakete giren satır")
    r = client.get("/api/v1/diagnostics/bundle", params={"log_lines": 50})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    disposition = r.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="sofascore-diagnostics-') and disposition.endswith('.zip"')
    files = _unzip(r.content)
    assert "pakete giren satır" in files["log_tail.txt"]
    assert json.loads(files["diagnostics.json"])["source"] == "web"

    assert client.get("/api/v1/diagnostics/bundle", params={"log_lines": diagnostics.MAX_TAIL_LINES + 1}).status_code == 422
    assert client.get("/api/v1/diagnostics/bundle", params={"log_lines": -1}).status_code == 422


@pytest.mark.parametrize("url", ["/api/v1/logs", "/api/v1/diagnostics", "/api/v1/diagnostics/bundle"])
def test_endpoints_are_read_only(url):
    for method in ("post", "put", "delete"):
        assert getattr(client, method)(url).status_code == 405


# --- CLI ---------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", ["tr", "en"])
def test_cli_messages_exist_in_both_languages(lang):
    with open(os.path.join(ROOT, "locales", f"{lang}.json"), encoding="utf-8") as f:
        strings = json.load(f)
    # "Log dosyasına bakın" ipucu artık dosyanın yolunu söyler
    assert "{path}" in strings["check_log_for_details"]
    assert strings["check_console_for_details"]
    assert "{path}" in strings["diagnostics_written"]
    assert "{error}" in strings["diagnostics_failed"]


def test_cli_flag_writes_the_bundle(tmp_path):
    # Göreli yol, komutun çalıştırıldığı dizine göre çözülür (main.py proje köküne chdir eder)
    env = dict(os.environ, LOG_DIR=str(tmp_path / "logs"), APP_LANGUAGE="en", PROXY_URL=PROXY, USE_PROXY="true")
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "main.py"), "--diagnostics", "report.zip"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    target = tmp_path / "report.zip"
    assert target.is_file()
    assert f"Diagnostics bundle written: {target}" in proc.stdout
    files = _unzip(target.read_bytes())
    doc = json.loads(files["diagnostics.json"])
    assert doc["source"] == "cli"
    assert doc["settings"]["values"]["PROXY_URL"] == "http://***@proxy.example.com:8080"
    assert "Pr0xy-P4ss!word" not in "".join(files.values())
