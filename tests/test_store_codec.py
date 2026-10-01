"""
src/store/codec.py: yük dosyalarının biçimi (docs/design/01-storage.md, bölüm 4.1).

Kurallı JSON baytları, belirlenimci gzip, sıkıştırılmamış baytların sha256'sı ve sonekten kodlama
seçen okuyucu. Ağ yok; yükler tests/fixtures/status altındaki gerçek /event yanıtlarıdır.
"""
from __future__ import annotations

import errno
import gzip
import hashlib
import json
import os
from pathlib import Path

import pytest

from src.exceptions import StorageError
from src.store import LayoutError, PayloadCorrupt, PayloadMissing, StoreError, codec

FIXTURES = Path(__file__).parent / "fixtures" / "status"
STATUS_FIXTURES = sorted(FIXTURES.rglob("*.json"))

PAYLOAD = {
    "event": {"id": 16416346, "homeTeam": {"name": "İstanbul Başakşehir"}, "awayTeam": {"name": "Göztepe"}},
    "z": 1, "a": [1.5, None, True, "x – y"],
}


def _leftovers(directory) -> list[str]:
    return sorted(n for n in os.listdir(directory) if n.endswith(".tmp"))


# --- kurallı baytlar ------------------------------------------------------------------------------

def test_canonical_bytes_are_compact_utf8_in_response_key_order():
    raw = codec.canonical_bytes(PAYLOAD)

    assert raw == json.dumps(PAYLOAD, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert "İstanbul Başakşehir".encode("utf-8") in raw  # kaçış yok
    assert b" " not in raw.replace("İstanbul Başakşehir".encode("utf-8"), b"").replace("x – y".encode("utf-8"), b"")
    assert raw.index(b'"z"') < raw.index(b'"a"')  # anahtarlar sıralanmaz: yanıttaki sıra korunur
    assert json.loads(raw) == PAYLOAD


def test_sha256_is_taken_over_the_uncompressed_bytes():
    encoded = codec.encode(PAYLOAD)

    assert encoded.raw == codec.canonical_bytes(PAYLOAD)
    assert encoded.sha256 == hashlib.sha256(encoded.raw).hexdigest() == codec.sha256_hex(encoded.raw)
    assert encoded.sha256 != hashlib.sha256(encoded.stored).hexdigest()
    assert (encoded.raw_bytes, encoded.stored_bytes) == (len(encoded.raw), len(encoded.stored))
    assert gzip.decompress(encoded.stored) == encoded.raw


def test_unserialisable_payload_raises_a_non_fatal_store_error_and_writes_nothing(tmp_path):
    target = tmp_path / "event.json.gz"

    for bad in ({"x": object()}, {"x": {1, 2}}, {"name": "\ud83d"}):  # son örnek: UTF-8'e çevrilemeyen yarım vekil
        with pytest.raises(StoreError) as caught:
            codec.write_payload(target, bad)
        assert caught.value.fatal is False

    assert os.listdir(tmp_path) == []


# --- yazma / okuma --------------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", STATUS_FIXTURES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_round_trip_of_every_status_fixture(fixture, tmp_path):
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    target = tmp_path / "event.json.gz"

    encoded = codec.write_payload(target, payload)

    assert codec.read_payload(target) == payload
    assert codec.read_raw(target) == encoded.raw == codec.canonical_bytes(payload)
    assert encoded.sha256 == hashlib.sha256(encoded.raw).hexdigest()
    assert target.read_bytes() == encoded.stored
    assert encoded.stored_bytes == target.stat().st_size
    with gzip.open(target, "rb") as f:  # Store olmadan da okunur: `zcat event.json.gz`
        assert json.loads(f.read()) == payload
    assert _leftovers(tmp_path) == []


def test_status_fixtures_are_present():
    """Yukarıdaki parametreli test, fixture dizini boşsa sessizce hiçbir şey denemezdi."""
    assert len(STATUS_FIXTURES) > 100
    assert {p.parent.name for p in STATUS_FIXTURES} >= {"football", "basketball", "tennis"}


def test_same_payload_twice_gives_byte_identical_files(tmp_path):
    first, second = tmp_path / "a" / "event.json.gz", tmp_path / "b" / "event.json.gz"

    codec.write_payload(first, PAYLOAD)
    os.utime(first, (1_000_000_000, 1_000_000_000))  # dosya zamanı çıktıyı etkilemez
    codec.write_payload(second, json.loads(json.dumps(PAYLOAD)))  # eşit ama ayrı nesne

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes()[4:8] == b"\x00\x00\x00\x00"  # gzip başlığındaki MTIME alanı
    assert codec.compress(codec.canonical_bytes(PAYLOAD)) == first.read_bytes()

    codec.write_payload(first, PAYLOAD)  # üzerine yazmak da aynı baytları verir
    assert first.read_bytes() == second.read_bytes()


def test_write_payload_creates_parent_directories_and_accepts_str_paths(tmp_path):
    target = os.path.join(str(tmp_path), "v3", "events", "16", "416", "16416346", "odds_all", "1.json.gz")

    codec.write_payload(target, PAYLOAD)

    assert codec.read_payload(target) == PAYLOAD


def test_writer_only_writes_gzip(tmp_path):
    for name in ("event.json", "event.json.zst", "event.txt"):
        with pytest.raises(LayoutError):
            codec.write_payload(tmp_path / name, PAYLOAD)
    assert os.listdir(tmp_path) == []


def test_write_failure_is_a_store_error_that_keeps_the_fatal_flag(tmp_path, monkeypatch):
    target = tmp_path / "event.json.gz"
    codec.write_payload(target, {"old": True})

    def disk_full(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "replace", disk_full)
    with pytest.raises(StoreError) as caught:
        codec.write_payload(target, PAYLOAD)
    monkeypatch.undo()

    assert caught.value.fatal is True
    assert caught.value.path == str(target)
    assert isinstance(caught.value, StorageError)  # "disk dolu → işi durdur" diyen çağıranlar bunu yakalar
    assert codec.read_payload(target) == {"old": True}
    assert _leftovers(tmp_path) == []


# --- bozuk ve eksik dosyalar ----------------------------------------------------------------------

def test_missing_file_raises_payload_missing(tmp_path):
    target = tmp_path / "event.json.gz"

    for read in (codec.read_payload, codec.read_raw):
        with pytest.raises(PayloadMissing) as caught:
            read(target)
        assert caught.value.path == str(target)
        assert caught.value.fatal is False


def test_every_truncation_of_a_gzip_file_raises_payload_corrupt(tmp_path):
    """Yarıda kesilmiş yazma ya da elektrik kesintisi: dosyanın her öneki (boş dosya dahil) bozuk sayılır."""
    target = tmp_path / "event.json.gz"
    stored = codec.encode(PAYLOAD).stored

    for length in range(len(stored)):
        target.write_bytes(stored[:length])
        for read in (codec.read_payload, codec.read_raw):
            with pytest.raises(PayloadCorrupt) as caught:
                read(target)
            assert caught.value.path == str(target)
            assert caught.value.fatal is False

    target.write_bytes(stored)
    assert codec.read_payload(target) == PAYLOAD


@pytest.mark.parametrize(
    "content",
    [
        b"garbage, not gzip at all",
        b"\x00" * 64,
        b'{"plain": "json without compression"}',
        gzip.compress(b'{"a": 1}') + b"trailing junk",
    ],
    ids=["text", "zeros", "uncompressed-json", "junk-after-member"],
)
def test_garbage_in_a_gzip_file_raises_payload_corrupt(tmp_path, content):
    target = tmp_path / "statistics.json.gz"
    target.write_bytes(content)

    with pytest.raises(PayloadCorrupt):
        codec.read_payload(target)
    with pytest.raises(PayloadCorrupt):
        codec.read_raw(target)


def test_bit_flip_inside_a_gzip_file_raises_payload_corrupt(tmp_path):
    target = tmp_path / "event.json.gz"
    stored = bytearray(codec.encode({"rows": list(range(500))}).stored)
    stored[len(stored) // 2] ^= 0xFF
    target.write_bytes(bytes(stored))

    with pytest.raises(PayloadCorrupt):  # deflate hatası ya da CRC uyuşmazlığı
        codec.read_payload(target)


@pytest.mark.parametrize(
    "raw",
    [b'{"a": 1', b"not json", b"\xff\xfe\x00", b'{"a": 1} {"b": 2}'],
    ids=["truncated-json", "text", "invalid-utf8", "two-documents"],
)
def test_valid_gzip_with_invalid_json_raises_payload_corrupt_on_parse(tmp_path, raw):
    target = tmp_path / "event.json.gz"
    target.write_bytes(gzip.compress(raw))

    with pytest.raises(PayloadCorrupt):
        codec.read_payload(target)
    assert codec.read_raw(target) == raw  # ham okuma ayrıştırmaz: açılabilen baytları aynen verir


def test_payload_corrupt_keeps_the_cause_and_is_a_storage_error(tmp_path):
    target = tmp_path / "event.json.gz"
    target.write_bytes(b"garbage")

    with pytest.raises(PayloadCorrupt) as caught:
        codec.read_payload(target)

    assert isinstance(caught.value, StoreError) and isinstance(caught.value, StorageError)
    assert caught.value.__cause__ is not None
    assert str(target) in str(caught.value)


# --- sonekten kodlama seçimi ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "name, expected",
    [("event.json.gz", "gzip"), ("1.json.zst", "zstd"), ("basic.json", "json"), ("manifest.json", "json")],
)
def test_codec_is_chosen_by_the_suffix(name, expected, tmp_path):
    assert codec.codec_of(name) == expected
    assert codec.codec_of(tmp_path / "a.gz.dir" / name) == expected  # yalnızca dosya adına bakılır


@pytest.mark.parametrize("name", ["event.gz", "event.jsonl.gz", "event.json.bz2", "event", "event.json.tmp"])
def test_unknown_suffix_is_a_layout_error(name, tmp_path):
    (tmp_path / name).write_bytes(b"{}")

    with pytest.raises(LayoutError):
        codec.codec_of(name)
    with pytest.raises(LayoutError):
        codec.read_payload(tmp_path / name)


def test_reader_accepts_legacy_plain_json(tmp_path):
    """Eski düzen: girintili, sıkıştırılmamış `basic.json` (src/fsutil.py'nin yazdığı biçim)."""
    target = tmp_path / "basic.json"
    text = json.dumps(PAYLOAD, ensure_ascii=False, indent=2)
    target.write_text(text, encoding="utf-8")

    assert codec.read_payload(target) == PAYLOAD
    assert codec.read_raw(target) == text.encode("utf-8")  # dosyadaki baytlar, yeniden yazılmadan


@pytest.mark.parametrize("content", [b"", b"   \n", b'{"a": [1, 2', b"\xff\xfe"], ids=["empty", "blank", "cut", "binary"])
def test_damaged_legacy_json_raises_payload_corrupt(tmp_path, content):
    target = tmp_path / "basic.json"
    target.write_bytes(content)

    with pytest.raises(PayloadCorrupt):
        codec.read_payload(target)


@pytest.mark.skipif(not codec.zstd_available(), reason="zstd modülü yok (Python < 3.14 ve backports.zstd kurulu değil)")
def test_reader_accepts_zstd_when_a_module_is_importable(tmp_path):
    target = tmp_path / "event.json.zst"
    raw = codec.canonical_bytes(PAYLOAD)
    stored = codec._zstd.compress(raw)
    target.write_bytes(stored)

    assert codec.read_payload(target) == PAYLOAD
    assert codec.read_raw(target) == raw

    for content in (stored[: len(stored) // 2], b"garbage", b""):
        target.write_bytes(content)
        with pytest.raises(PayloadCorrupt):
            codec.read_payload(target)


def test_zstd_file_without_a_zstd_module_names_the_missing_package(tmp_path, monkeypatch):
    target = tmp_path / "event.json.zst"
    target.write_bytes(b"\x28\xb5\x2f\xfd whatever")
    monkeypatch.setattr(codec, "_zstd", None)

    assert codec.zstd_available() is False
    with pytest.raises(StoreError) as caught:
        codec.read_payload(target)

    assert not isinstance(caught.value, PayloadCorrupt)  # dosya bozuk değil, okuyacak modül yok
    assert "backports.zstd" in str(caught.value)
    assert caught.value.fatal is False
