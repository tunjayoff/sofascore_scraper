"""
src/store/manifest.py: varlık başına manifest.json (docs/design/01-storage.md, bölüm 4.2), biçim 1.

Veri sınıfları, okuma, yazma ve doğrulama. Manifest kaynaktır (katalog onu yansıtır), bu yüzden bozuk
bir manifest sessizce kabul edilmemeli ve bu sürümün tanımadığı alanlar yeniden yazarken kaybolmamalıdır.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from src.store import LayoutError, PayloadCorrupt, PayloadMissing, SchemaTooNew, manifest
from src.store.manifest import EmptyMark, ErrorMark, HistoryMark, Manifest, Observation, SliceEntry

SHA_A = "a" * 64
SHA_B = "0123456789abcdef" * 4

# Tasarım belgesindeki örnek ("..." yerlerine gerçek değerler konmuş hali)
EXAMPLE = {
    "format": 1,
    "kind": "event",
    "id": 16416346,
    "created_at": "2026-09-29T17:55:14+00:00",
    "updated_at": "2026-10-01T12:00:03+00:00",
    "migrated_from": "match_details/8_LaLiga/season_LaLiga_26_27/16416346",
    "observation": {"observed_at_utc": "2026-10-01T12:00:03+00:00", "change_ts": 1789594338, "status_regressed": False},
    "slices": {
        "event": {"state": "ok", "fetched_at": "2026-10-01T12:00:03+00:00", "checked_at": "2026-10-01T12:00:03+00:00",
                  "bytes": 1432, "raw_bytes": 7187, "sha256": SHA_A},
        "pregame_form": {"state": "empty", "checked_at": "2026-09-30T08:00:00+00:00",
                         "empty": {"count": 2, "unverified": 0, "reason": "404", "at": "2026-09-30T08:00:00+00:00"}},
        "statistics": {"state": "error", "checked_at": "2026-10-01T11:59:00+00:00",
                       "empty": {"count": 1, "unverified": 0},
                       "error": {"reason": "429", "status": 429, "at": "2026-10-01T11:59:00+00:00", "count": 3}},
        "odds_all/1": {"state": "ok", "fetched_at": "2026-10-01T12:00:01+00:00", "bytes": 911, "raw_bytes": 5120,
                       "sha256": SHA_B, "history": {"count": 12, "last_sha256": SHA_B}},
    },
}

T0 = datetime(2026, 9, 29, 17, 55, 14, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 1, 12, 0, 3, tzinfo=timezone.utc)


def _example() -> dict:
    return copy.deepcopy(EXAMPLE)


def _manifest() -> Manifest:
    return Manifest(
        kind="event", id=16416346, created_at=T0, updated_at=T1,
        observation=Observation(observed_at=T1, change_ts=1789594338),
        slices={
            "event": SliceEntry("ok", fetched_at=T1, checked_at=T1, stored_bytes=1432, raw_bytes=7187, sha256=SHA_A),
            "lineups": SliceEntry("empty", checked_at=T1, empty=EmptyMark(count=1, reason="404", at=T1)),
        },
    )


# --- tasarımdaki örnek ----------------------------------------------------------------------------

def test_design_example_parses_into_dataclasses():
    parsed = manifest.from_dict(_example())

    assert (parsed.format, parsed.kind, parsed.id) == (1, "event", 16416346)
    assert (parsed.created_at, parsed.updated_at) == (T0, T1)
    assert parsed.migrated_from == "match_details/8_LaLiga/season_LaLiga_26_27/16416346"
    assert parsed.observation == Observation(observed_at=T1, change_ts=1789594338, status_regressed=False)
    assert list(parsed.slices) == ["event", "pregame_form", "statistics", "odds_all/1"]

    event = parsed.slices["event"]
    assert (event.state, event.stored_bytes, event.raw_bytes, event.sha256) == ("ok", 1432, 7187, SHA_A)
    assert event.fetched_at == event.checked_at == T1
    assert event.has_payload and (event.empty, event.error, event.history, event.meta) == (None, None, None, None)

    form = parsed.slices["pregame_form"]
    assert form.state == "empty" and not form.has_payload and form.fetched_at is None
    assert form.empty == EmptyMark(count=2, unverified=0, reason="404", at=datetime(2026, 9, 30, 8, tzinfo=timezone.utc))

    stats = parsed.slices["statistics"]
    assert stats.empty == EmptyMark(count=1, unverified=0)
    assert stats.error == ErrorMark(reason="429", status=429, at=datetime(2026, 10, 1, 11, 59, tzinfo=timezone.utc), count=3)

    assert parsed.slices["odds_all/1"].history == HistoryMark(count=12, last_sha256=SHA_B)
    assert manifest.validate(parsed) == []


def test_design_example_round_trips_to_the_same_json():
    assert manifest.to_dict(manifest.from_dict(_example())) == EXAMPLE


# --- yazma / okuma --------------------------------------------------------------------------------

def test_write_then_read_gives_an_equal_manifest(tmp_path):
    target = tmp_path / "v3" / "events" / "16" / "416" / "16416346" / "manifest.json"
    original = _manifest()

    manifest.write_manifest(target, original)

    assert manifest.read_manifest(target) == original
    assert manifest.read_manifest(str(target)) == original
    assert os.listdir(target.parent) == ["manifest.json"]


def test_file_is_pretty_uncompressed_utf8_json(tmp_path):
    target = tmp_path / "manifest.json"
    original = _manifest()
    original.migrated_from = "match_details/52_Süper Lig/season_Süper Lig_26_27/16416346"

    manifest.write_manifest(target, original)
    text = target.read_text(encoding="utf-8")

    assert text.startswith('{\n  "format": 1,\n  "kind": "event",\n  "id": 16416346,')
    assert text.endswith("}\n")
    assert "Süper Lig" in text  # ensure_ascii=False
    data = json.loads(text)
    assert list(data) == ["format", "kind", "id", "created_at", "updated_at", "migrated_from", "observation", "slices"]
    assert data["slices"]["lineups"] == {
        "state": "empty", "checked_at": "2026-10-01T12:00:03+00:00",
        "empty": {"count": 1, "unverified": 0, "reason": "404", "at": "2026-10-01T12:00:03+00:00"},
    }  # None olan alanlar yazılmaz


def test_same_manifest_twice_gives_byte_identical_files(tmp_path):
    manifest.write_manifest(tmp_path / "a.json", _manifest())
    manifest.write_manifest(tmp_path / "b.json", _manifest())

    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_minimal_manifest_of_every_kind(tmp_path):
    for kind in ("event", "tournament", "season", "team", "player", "sport"):
        target = tmp_path / kind / "manifest.json"
        original = Manifest(kind=kind, id=17, created_at=T0, updated_at=T0)

        manifest.write_manifest(target, original)

        assert manifest.read_manifest(target) == original
        assert json.loads(target.read_text(encoding="utf-8")) == {
            "format": 1, "kind": kind, "id": 17, "created_at": "2026-09-29T17:55:14+00:00",
            "updated_at": "2026-09-29T17:55:14+00:00", "slices": {},
        }


def test_slice_meta_and_an_empty_payload_are_stored(tmp_path):
    """Boş 200 yanıtı: state empty ama yük dosyası var. meta: durumun yanındaki küçük JSON (ör. tur sayfası)."""
    original = Manifest(kind="season", id=76986, created_at=T0, updated_at=T1, slices={
        "schedule/round_12": SliceEntry("ok", fetched_at=T1, stored_bytes=10, raw_bytes=20, sha256=SHA_A,
                                        meta={"complete": True}),
        "standings/total": SliceEntry("empty", fetched_at=T1, checked_at=T1, stored_bytes=22, raw_bytes=2, sha256=SHA_B,
                                      empty=EmptyMark(count=1, reason="empty", at=T1)),
    })

    manifest.write_manifest(tmp_path / "manifest.json", original)
    loaded = manifest.read_manifest(tmp_path / "manifest.json")

    assert loaded == original
    assert loaded.slices["schedule/round_12"].meta == {"complete": True}
    assert loaded.slices["standings/total"].has_payload is True


# --- zamanlar --------------------------------------------------------------------------------------

def test_timestamps_are_written_in_utc_and_keep_sub_second_precision(tmp_path):
    istanbul = timezone(timedelta(hours=3))
    original = _manifest()
    original.updated_at = datetime(2026, 10, 1, 15, 0, 3, 250000, tzinfo=istanbul)

    manifest.write_manifest(tmp_path / "manifest.json", original)
    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    loaded = manifest.read_manifest(tmp_path / "manifest.json")

    assert data["updated_at"] == "2026-10-01T12:00:03.250000+00:00"
    assert loaded.updated_at == original.updated_at  # aynı an
    assert loaded.updated_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("text", ["2026-10-01T12:00:03Z", "2026-10-01T12:00:03", "2026-10-01T15:00:03+03:00"])
def test_reader_accepts_z_suffix_naive_utc_and_other_offsets(text):
    data = _example()
    data["updated_at"] = text

    assert manifest.from_dict(data).updated_at == T1


# --- ileriye dönük uyum ----------------------------------------------------------------------------

def test_unknown_fields_survive_a_read_modify_write(tmp_path):
    """Alan eklemek biçim numarasını değiştirmez: eski sürüm, yeni sürümün yazdığı alanları silmemeli."""
    data = _example()
    data["tournament_id"] = 8
    data["observation"]["source"] = "live"
    data["slices"]["event"]["via"] = "bridge"
    data["slices"]["statistics"]["error"]["retry_after"] = 30
    data["slices"]["statistics"]["empty"]["note"] = "x"
    data["slices"]["odds_all/1"]["history"]["first_at"] = "2026-09-01T00:00:00+00:00"
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(data), encoding="utf-8")

    loaded = manifest.read_manifest(target)
    loaded.slices["lineups"] = SliceEntry("ok", fetched_at=T1, stored_bytes=1, raw_bytes=2, sha256=SHA_A)
    manifest.write_manifest(target, loaded)

    assert loaded.extra == {"tournament_id": 8}
    assert loaded.slices["event"].extra == {"via": "bridge"}
    written = json.loads(target.read_text(encoding="utf-8"))
    lineups = written["slices"].pop("lineups")
    assert written == data
    assert lineups == {"state": "ok", "fetched_at": "2026-10-01T12:00:03+00:00", "bytes": 1, "raw_bytes": 2, "sha256": SHA_A}


def test_extra_cannot_override_a_known_field():
    original = _manifest()
    original.extra = {"kind": "team", "note": "x"}
    original.slices["event"].extra = {"state": "error"}

    data = manifest.to_dict(original)

    assert (data["kind"], data["note"]) == ("event", "x")
    assert data["slices"]["event"]["state"] == "ok"


def test_newer_format_is_refused_with_schema_too_new(tmp_path):
    data = _example()
    data["format"] = 2
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SchemaTooNew) as caught:
        manifest.read_manifest(target)

    assert (caught.value.component, caught.value.found, caught.value.supported) == ("manifest", 2, manifest.MANIFEST_FORMAT)
    assert caught.value.path == str(target)


# --- doğrulama -------------------------------------------------------------------------------------

def _set(path, value):
    """`a.b.c` yolundaki (ya da demet olarak verilen) alanı değiştiren, value=_DROP ise silen bir işlev döndürür."""
    def apply(data: dict) -> None:
        *parents, last = path.split(".") if isinstance(path, str) else path
        node = data
        for part in parents:
            node = node[part]
        if value is _DROP:
            del node[last]
        else:
            node[last] = value
    return apply


_DROP = object()

INVALID = {
    "format-missing": _set("format", _DROP),
    "format-zero": _set("format", 0),
    "format-text": _set("format", "1"),
    "format-bool": _set("format", True),
    "kind-unknown": _set("kind", "league"),
    "kind-missing": _set("kind", _DROP),
    "id-text": _set("id", "16416346"),
    "id-negative": _set("id", -1),
    "id-float": _set("id", 16416346.0),
    "id-bool": _set("id", True),
    "created-missing": _set("created_at", _DROP),
    "created-not-a-time": _set("created_at", "yesterday"),
    "updated-number": _set("updated_at", 1789594338),
    "migrated-empty": _set("migrated_from", ""),
    "migrated-number": _set("migrated_from", 5),
    "observation-list": _set("observation", []),
    "observation-time": _set("observation.observed_at_utc", "soon"),
    "observation-change-ts": _set("observation.change_ts", "1789594338"),
    "observation-flag": _set("observation.status_regressed", "no"),
    "slices-list": _set("slices", []),
    "slice-not-object": _set("slices.event", "ok"),
    "slice-name-upper": _set("slices.Event", {"state": "empty"}),
    "slice-name-traversal": _set(("slices", "../x"), {"state": "empty"}),
    "slice-name-empty-sub": _set("slices.odds_all/", {"state": "empty"}),
    "state-unknown": _set("slices.event.state", "not_requested"),
    "state-missing": _set("slices.event.state", _DROP),
    "ok-without-payload": _set("slices.pregame_form.state", "ok"),
    "error-without-mark": _set("slices.pregame_form.state", "error"),
    "sha-short": _set("slices.event.sha256", "abc"),
    "sha-upper": _set("slices.event.sha256", "A" * 64),
    "sha-missing": _set("slices.event.sha256", _DROP),
    "bytes-missing": _set("slices.event.bytes", _DROP),
    "bytes-negative": _set("slices.event.bytes", -1),
    "raw-bytes-text": _set("slices.event.raw_bytes", "7187"),
    "fetched-not-a-time": _set("slices.event.fetched_at", "12:00"),
    "checked-number": _set("slices.event.checked_at", 5),
    "empty-list": _set("slices.pregame_form.empty", [2]),
    "empty-count-negative": _set("slices.pregame_form.empty.count", -1),
    "empty-unverified-text": _set("slices.pregame_form.empty.unverified", "0"),
    "empty-reason-number": _set("slices.pregame_form.empty.reason", 404),
    "empty-at": _set("slices.pregame_form.empty.at", "then"),
    "error-text": _set("slices.statistics.error", "429"),
    "error-reason-missing": _set("slices.statistics.error.reason", _DROP),
    "error-reason-empty": _set("slices.statistics.error.reason", ""),
    "error-status-text": _set("slices.statistics.error.status", "429"),
    "error-count-zero": _set("slices.statistics.error.count", 0),
    "error-at": _set("slices.statistics.error.at", "then"),
    "history-number": _set("slices.odds_all/1.history", 12),
    "history-count": _set("slices.odds_all/1.history.count", -1),
    "history-sha": _set("slices.odds_all/1.history.last_sha256", "xyz"),
    "meta-list": _set("slices.event.meta", [1]),
}


@pytest.mark.parametrize("mutate", INVALID.values(), ids=INVALID.keys())
def test_invalid_manifest_is_rejected_on_read(mutate, tmp_path):
    data = _example()
    mutate(data)
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PayloadCorrupt) as caught:
        manifest.read_manifest(target)

    assert caught.value.path == str(target)
    assert caught.value.detail  # hangi alanın bozuk olduğunu söyler
    assert caught.value.fatal is False


@pytest.mark.parametrize("data", [[], "manifest", 5, None], ids=["list", "text", "number", "null"])
def test_manifest_must_be_a_json_object(data, tmp_path):
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PayloadCorrupt):
        manifest.read_manifest(target)


def test_truncated_and_garbage_files_raise_payload_corrupt(tmp_path):
    target = tmp_path / "manifest.json"
    manifest.write_manifest(target, _manifest())
    content = target.read_bytes()

    for damaged in (b"", content[: len(content) // 2], content[:-3], b"\x1f\x8b\x08garbage", b"\xff\xfe"):
        target.write_bytes(damaged)
        with pytest.raises(PayloadCorrupt) as caught:
            manifest.read_manifest(target)
        assert caught.value.path == str(target)


def test_missing_manifest_raises_payload_missing(tmp_path):
    with pytest.raises(PayloadMissing):
        manifest.read_manifest(tmp_path / "manifest.json")


@pytest.mark.parametrize(
    "spoil",
    [
        lambda m: setattr(m, "kind", "league"),
        lambda m: setattr(m, "id", -5),
        lambda m: setattr(m, "created_at", datetime(2026, 10, 1, 12, 0, 3)),  # saat dilimi yok
        lambda m: setattr(m, "updated_at", "2026-10-01T12:00:03+00:00"),  # metin, datetime değil
        lambda m: setattr(m, "observation", {"observed_at_utc": None}),
        lambda m: m.slices.__setitem__("Bad Name", SliceEntry("empty")),
        lambda m: m.slices.__setitem__("statistics", {"state": "ok"}),
        lambda m: m.slices.__setitem__("statistics", SliceEntry("ok")),
        lambda m: m.slices.__setitem__("statistics", SliceEntry("error")),
        lambda m: m.slices.__setitem__("statistics", SliceEntry("missing")),
        lambda m: setattr(m.slices["event"], "sha256", None),
        lambda m: setattr(m.slices["lineups"].empty, "count", 1.5),
    ],
    ids=["kind", "id", "naive-time", "text-time", "observation-dict", "slice-name", "slice-dict", "ok-without-payload",
         "error-without-mark", "state", "partial-payload", "float-count"],
)
def test_invalid_manifest_cannot_be_written_and_the_old_file_stays(spoil, tmp_path):
    target = tmp_path / "manifest.json"
    manifest.write_manifest(target, _manifest())
    before = target.read_bytes()
    broken = _manifest()
    spoil(broken)

    assert manifest.validate(broken) != []
    with pytest.raises(LayoutError):
        manifest.write_manifest(target, broken)

    assert target.read_bytes() == before
    assert os.listdir(tmp_path) == ["manifest.json"]


def test_validate_lists_every_problem_without_raising():
    broken = _manifest()
    broken.kind = "league"
    broken.slices["statistics"] = SliceEntry("error")
    broken.slices["event"].state = "done"

    problems = manifest.validate(broken)

    assert len(problems) == 3
    assert any("kind" in p for p in problems)
    assert any("'statistics'" in p for p in problems) and any("'event'" in p for p in problems)
    assert manifest.validate(_manifest()) == []


def test_written_manifest_always_carries_the_current_format():
    original = _manifest()

    assert manifest.to_dict(original)["format"] == manifest.MANIFEST_FORMAT == 1
