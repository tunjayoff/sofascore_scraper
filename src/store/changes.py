"""
Değişiklik günlüğünün katalogdaki dizini (docs/design/01-storage.md, bölüm 3.4 adım 6, 5.2 ve 8.5).

Günlüğün kendisi dosyadır; `changes` tablosu o dosyaların dizinidir ve dosyalardan yeniden kurulur. Bu adımda
tek kaynak eski düzendeki `score_changes.jsonl`'dır: satırın sıra numarası (`seq`) dosyadaki satır
numarasıdır (1'den başlar). Boş ve okunamayan satırlar da numara harcar, böylece bir satır bozulduğunda
ötekilerin numarası kaymaz ve yeniden kurma aynı numaraları verir. v3'ün aylık parçaları
(`changes/<yyyy>-<aa>.jsonl`, satırın içinde `seq` ile) onları yazan adımla birlikte buraya eklenir.

`ChangeLog` (`Store.changes`) günlüğün okuma API'sidir (bölüm 2.3): `list` ve `last_seq` bu dizinden
okur. Yazma yöntemi (`append`) sonraki adımlardadır.

Dizine giremeyen satır (tarihi ya da maç kimliği olmayan) atlanır ve bildirilir; numarası yine harcanır.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Tuple

from src.store import legacy
from src.store.catalog import Catalog
from src.store.entities import storable
from src.store.legacy import LegacyLine, LegacyProblem, LegacyReader

if TYPE_CHECKING:
    from src.store.api import Store

Row = Dict[str, Any]

LEGACY_SEGMENT = legacy.CHANGES_FILE  # `changes.segment`: satırın geldiği dosya, DATA_DIR'e göre

_INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1


def _plain_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if _INT64_MIN <= value <= _INT64_MAX else None


def _epoch(value: Any) -> Optional[int]:
    """`ts_utc` (ISO 8601; saat dilimi yoksa UTC) → epoch saniye; ayrıştırılamıyorsa None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return math.floor(parsed.timestamp())
    except (OverflowError, OSError):
        return None


def change_row(seq: int, row: Mapping[str, Any], line: str, segment: str) -> Optional[Row]:
    """
    Günlüğün bir satırından (`src/refresh.change_row` biçimi) `changes` tablosu satırı. `ts_utc` ya da
    `event_id` kullanılamıyorsa None (iki sütun da NOT NULL). `fields` değişen alan adlarıdır, satırdaki
    sırayla ve virgülle ayrılmış; `row_json` satırın dosyada yazıldığı halidir.
    """
    ts = _epoch(row.get("ts_utc"))
    event_id = _plain_int(row.get("event_id"))
    if ts is None or event_id is None:
        return None
    tournament = row.get("tournament")
    changed = row.get("changed")
    sport = row.get("sport")
    return storable({
        "seq": seq,
        "ts": ts,
        "event_id": event_id,
        "sport": sport if isinstance(sport, str) else None,
        "tournament_id": _plain_int(tournament.get("id")) if isinstance(tournament, Mapping) else None,
        "status_regressed": int(bool(row.get("status_regressed"))),
        "fields": ",".join(str(name) for name in changed) if isinstance(changed, Mapping) else "",
        "row_json": line,
        "segment": segment,
    })


def legacy_rows(lines: List[LegacyLine], problems: Optional[List[LegacyProblem]] = None) -> List[Row]:
    """`LegacyReader.change_log` satırlarından tablo satırları; kullanılamayan satır `problems`a yazılır."""
    rows: List[Row] = []
    for entry in lines:
        row = change_row(entry.seq, entry.row, entry.line, LEGACY_SEGMENT)
        if row is None:
            if problems is not None:
                problems.append(LegacyProblem(LEGACY_SEGMENT, legacy.PROBLEM_MALFORMED,
                                              f"satır {entry.seq}: ts_utc ya da event_id yok"))
            continue
        rows.append(row)
    return rows


def index_legacy(cat: Catalog, reader: LegacyReader, problems: Optional[List[LegacyProblem]] = None) -> int:
    """
    `score_changes.jsonl`'ı baştan dizinler: o dosyadan gelen satırlar silinir ve yeniden yazılır (numaralar
    satır numarası olduğu için aynı kalır). `Catalog.write()` bloğunun içinde çağrılır; yazılan satır
    sayısını döndürür. Dosya yoksa tabloda o dosyanın satırı kalmaz.
    """
    report = legacy.LegacyReport()
    lines = reader.change_log(report)
    if problems is not None:
        problems.extend(report.problems)
    rows = legacy_rows(lines, problems)
    cat.connection().execute("DELETE FROM changes WHERE segment = ?", (LEGACY_SEGMENT,))
    cat.upsert("changes", rows)
    return len(rows)


def index_all(cat: Catalog, reader: LegacyReader, problems: Optional[List[LegacyProblem]] = None) -> int:
    """Bütün günlük dosyalarını dizinler (bölüm 3.4, adım 6): önce eski düzen dosyası, sonra v3 parçaları."""
    return index_legacy(cat, reader, problems)


# --- okuma API'si -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ChangeRow:
    """
    Değişiklik günlüğünün bir satırı. `row` günlükteki satırın kendisidir (`src/refresh.change_row` biçimi:
    `changed` alan → [eski, yeni]); öteki alanlar dizinin sütunlarıdır. `ts` epoch saniyedir (UTC).
    """

    seq: int
    ts: int
    event_id: int
    sport: Optional[str]
    tournament_id: Optional[int]
    status_regressed: bool
    fields: Tuple[str, ...]  # değişen alan adları, satırdaki sırayla
    row: Mapping[str, Any]
    segment: str  # satırın geldiği dosya, DATA_DIR'e göre


_CHANGE_SELECT = ("SELECT seq, ts, event_id, sport, tournament_id, status_regressed, fields, row_json, segment "
                  "FROM changes")


def _change(row: Any) -> ChangeRow:
    try:
        parsed = json.loads(row[7])
    except ValueError:
        parsed = None
    return ChangeRow(
        seq=int(row[0]), ts=int(row[1]), event_id=int(row[2]), sport=row[3], tournament_id=row[4],
        status_regressed=bool(row[5]), fields=tuple(name for name in str(row[6]).split(",") if name),
        row=parsed if isinstance(parsed, dict) else {}, segment=str(row[8]),
    )


def _whole(value: Any, what: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{what}: expected an integer >= {minimum}, got {value!r}")
    return value


class ChangeLog:
    """Değişiklik günlüğünün okuma API'si (`Store.changes`); satırlar katalogdaki dizinden gelir."""

    def __init__(self, store: "Store") -> None:
        self._store = store
        self._catalog = store._catalog

    def list(self, *, after_seq: int = 0, event_id: Optional[int] = None, since: Optional[float] = None,
             limit: int = 1000) -> List[ChangeRow]:
        """
        Sıra numarası `after_seq`'ten büyük satırlar, sıra numarasıyla; en çok `limit` tane. Tüketici son
        satırın `seq`'ini bir sonraki çağrıya `after_seq` olarak verir. event_id: yalnızca o maçın satırları;
        since: zamanı (epoch saniye) bundan önce olmayan satırlar.
        """
        conditions = ["seq > ?"]
        params: List[Any] = [_whole(after_seq, "after_seq", 0)]
        if event_id is not None:
            conditions.append("event_id = ?")
            params.append(_whole(event_id, "event_id", -(2 ** 63)))
        if since is not None:
            if isinstance(since, bool) or not isinstance(since, (int, float)) or since != since:
                raise ValueError(f"since: expected a number, got {since!r}")
            conditions.append("ts >= ?")
            params.append(since)
        params.append(_whole(limit, "limit", 1))
        self._store._require_open()
        assert self._catalog is not None
        with self._catalog.read() as conn:
            found = conn.execute(
                f"{_CHANGE_SELECT} WHERE {' AND '.join(conditions)} ORDER BY seq LIMIT ?", params).fetchall()
        return [_change(row) for row in found]

    def last_seq(self) -> int:
        """Dizindeki en büyük sıra numarası; günlük boşsa 0."""
        self._store._require_open()
        assert self._catalog is not None
        with self._catalog.read() as conn:
            row = conn.execute("SELECT max(seq) FROM changes").fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0


__all__ = [
    "ChangeRow",
    "ChangeLog",
    "LEGACY_SEGMENT",
    "change_row",
    "legacy_rows",
    "index_legacy",
    "index_all",
]
