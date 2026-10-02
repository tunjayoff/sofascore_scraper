"""
Karakterizasyon testleri: bugünkü davranışı (istek sırası, yazılan dosyalar) sabitler; yeniden yazımlar
bu testler yeşilken yapılır (docs/design/03-implementation-plan.md, kural 1).

Bu modül testlerin ortak araçlarını tutar: sahte dünyanın yolu, golden karşılaştırması ve veri
dizininin karşılaştırılabilir özeti.

Goldenları yeniden üretmek: `UPDATE_GOLDENS=1 python -m pytest tests/characterization`. Üretilen fark
gözden geçirilmeden commit edilmez: golden değiştiyse davranış değişmiştir.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

FETCH_FIXTURES = Path(__file__).parent / "fixtures" / "fetch"
WORLD = FETCH_FIXTURES / "world.json"
UPDATE_ENV = "UPDATE_GOLDENS"

# Çalıştırma anına bağlı alanlar: değerleri değil varlıkları karşılaştırılır
_VOLATILE_KEYS = frozenset({"observed_at_utc", "at", "ts_utc"})
# İçeriği kodun ürettiği küçük dosyalar goldende açık yazılır; gerisi (SofaScore yanıtının kopyaları,
# dışa aktarılan CSV'ler) özetle (sha256'nın ilk 12 hanesi) karşılaştırılır.
_INLINE_NAMES = frozenset({"_unavailable.json", "_slice_status.json", "observation.json", "score_changes.jsonl"})
# v3 varlık dizininin manifesti (ST-22) açık yazılır: dilimlerin durumu, meta'sı ve yük özetleri (sha256) goldende
# görünür; zamanları çalıştırma anına, sıkıştırılmış boyutu (`bytes`) platformun zlib'ine bağlıdır. Yük dosyaları
# (`.json.gz`) yalnızca varlıklarıyla karşılaştırılır: içerikleri manifestteki `sha256` ve `raw_bytes` ile sabittir.
_MANIFEST_NAME = "manifest.json"
_MANIFEST_VOLATILE_KEYS = frozenset({"created_at", "updated_at", "fetched_at", "checked_at", "at", "bytes"})
# Yerel saatle yazılan tarihler (özet CSV'deki match_date) makinenin saat dilimine göre değişir
_LOCAL_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?")
# Dosya adındaki çalıştırma zamanı (processed/all_matches_<epoch>.csv)
_EPOCH_IN_NAME = re.compile(r"_\d{9,}(?=\.)")
_TEXT_SUFFIXES = frozenset({".json", ".jsonl", ".csv", ".txt"})
# Veri dizinindeki durum dosyaları (iş geçmişi; ileride katalog) indirilen veri değildir: özete girmez
STATE_DIR = ".meta"


def assert_golden(name: str, actual: Dict[str, Any]) -> None:
    """`actual`ı fixtures/fetch/{name}.golden.json ile karşılaştırır; UPDATE_GOLDENS=1 ise dosyayı yazar."""
    path = FETCH_FIXTURES / f"{name}.golden.json"
    text = json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if os.environ.get(UPDATE_ENV):
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    assert path.exists(), f"golden missing: {path} (run with {UPDATE_ENV}=1 and review the result)"
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert json.loads(text) == expected, f"{path.name} differs; if the change is intended, regenerate with {UPDATE_ENV}=1"


def _mask(value: Any, keys: frozenset = _VOLATILE_KEYS) -> Any:
    if isinstance(value, dict):
        return {k: "<volatile>" if k in keys else _mask(v, keys) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v, keys) for v in value]
    return value


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _file_summary(path: Path) -> Any:
    if path.suffix not in _TEXT_SUFFIXES:
        return "<binary>"  # yalnızca varlığı karşılaştırılır
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if path.suffix == ".json":
        data = _mask(json.loads(text))
        if path.name in _INLINE_NAMES:
            return data
        if path.name == _MANIFEST_NAME:
            return _mask(data, _MANIFEST_VOLATILE_KEYS)
        if path.name.endswith("_summary.json") and isinstance(data, list):
            # Tur sonuçları eşzamanlı isteklerin bitiş sırasıyla yazılır: sıra sözleşme değil
            data = sorted(data, key=lambda item: json.dumps(item, sort_keys=True))
        return _digest(json.dumps(data, ensure_ascii=False, sort_keys=True))
    if path.suffix == ".jsonl":
        rows: List[Any] = [_mask(json.loads(line)) for line in text.splitlines() if line.strip()]
        return rows
    if path.suffix == ".csv":
        # Satır sırası eşzamanlı isteklerin bitiş sırasına bağlı olabilir: başlık + sıralı satırlar
        lines = _LOCAL_DATETIME.sub("<local-datetime>", text).splitlines()
        return _digest("\n".join(lines[:1] + sorted(lines[1:])))
    return _digest(text)


def snapshot_tree(root: Any) -> Dict[str, Any]:
    """
    Veri dizininin özeti: göreli yol ("/" ile) → içerik (küçük, kodun ürettiği dosyalar) ya da içerik özeti.
    `.meta/` altı sayılmaz.
    """
    base = Path(root)
    return {
        _EPOCH_IN_NAME.sub("_<epoch>", p.relative_to(base).as_posix()): _file_summary(p)
        for p in sorted(base.rglob("*"))
        if p.is_file() and STATE_DIR not in p.relative_to(base).parts
    }


def pin_default_settings(monkeypatch: Any) -> None:
    """Goldenların dayandığı ayarları varsayılanlarına sabitler: kabuktan ya da başka testten sızan değer sonucu değiştirmesin."""
    import src.utils as utils

    for key in (
        "MAX_RETRIES", "REQUEST_TIMEOUT", "WAIT_TIME_MIN", "WAIT_TIME_MAX",
        "RATE_LIMIT_THRESHOLD_CONSECUTIVE", "RATE_LIMIT_THRESHOLD_RATIO", "SERVER_ERROR_THRESHOLD_CONSECUTIVE",
        "IGNORE_RATE_LIMIT", "REFRESH_WINDOW_HOURS", "REFRESH_MIN_INTERVAL_HOURS", "REFRESH_LEGACY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAX_CONCURRENT", "5")
    monkeypatch.setattr(utils, "FETCH_ONLY_FINISHED", True)
    monkeypatch.setattr(utils, "SAVE_EMPTY_ROUNDS", False)
