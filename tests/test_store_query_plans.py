"""
Katalogun sıcak sorguları adı verilen dizinleri kullanır (docs/design/01-storage.md, bölüm 3.7).

Her sorgu için `EXPLAIN QUERY PLAN` çıktısında beklenen dizinin adı aranır. Sorgu metinleri bölüm 3.7'deki
tablonun biçimleridir; okuma API'si (EventStore, plan maddesi ST-30) aynı biçimleri kurar ve kendi
sorgularını bu dosyadaki `plan` yardımcısıyla denetleyebilir.

Katalog sentetik satırlarla doldurulur (4 spor, 40 turnuva, 7.200 olay, 30.240 dilim satırı) ve `ANALYZE`
çalıştırılır; belgedeki ölçüm 301.000 olayla yapıldı. Zaman ölçülmez: yalnızca planlayıcının seçtiği
dizin sabitlenir.

Kısmi dizinler yalnızca sorgu, dizinin koşulunu planlayıcının tanıyacağı biçimde yazarsa kullanılır.
Aşağıdaki "yazım" testleri bunu iki yönden sabitler: doğru yazım dizini kullanır; mantıkça aynı ama
başka yazılmış sorgu (SQLite 3.53'e kadar) kullanmaz. İkinci tür bir test, planlayıcı akıllandığı için
kırılırsa kural gevşetilebilir ve o satır silinir.

ATTACH edilen sorgu ve akış okuma sorgusu `state.db` tablolarına (follows, stream_events) dokunur. O dosya
burada da `StateDb` ile, yani gerçek göç betiğiyle (sofascore_scraper/store/migrations/state/0001_initial.sql) kurulur;
DDL'in testte ayrı bir kopyası yoktur. state.db'de `ANALYZE` çalıştırılmaz (tasarım da çalıştırmıyor):
istatistik varken ve akışlar eşit doluyken planlayıcı akış okuma sorgusunda `stream_events_stream` yerine
rowid aralığını seçer.
"""
from __future__ import annotations

import re
import sqlite3

import pytest

from sofascore_scraper.store import catalog
from sofascore_scraper.store.catalog import Catalog
from sofascore_scraper.store.state import StateDb

SPORTS = ("football", "basketball", "tennis", "handball")
TOURNAMENTS = 40
SEASONS_PER_TOURNAMENT = 3
EVENTS_PER_SEASON = 60
TEAMS_PER_TOURNAMENT = 20
SLICE_KEYS = ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents")
BASE_TS = 1_780_000_000
NOW = BASE_TS + 400 * 86400

# --- sentetik katalog -----------------------------------------------------------------------------------

def _status(n: int) -> str:
    bucket = n % 100
    if bucket < 80:
        return "completed"
    if bucket < 85:
        return "decided_without_play"
    if bucket < 90:
        return "void"
    if bucket < 95:
        return "not_started"
    if bucket < 98:
        return "live"
    return "unknown"


def _rows():
    tournaments, seasons, participants, events, event_participants, slices, changes = [], [], [], [], [], [], []
    event_id = 10_000_000
    for t in range(1, TOURNAMENTS + 1):
        sport = SPORTS[t % len(SPORTS)]
        tournaments.append({"id": t, "sport": sport, "category_id": t % 7, "name": f"Tournament {t}",
                            "name_folded": f"tournament {t}", "slug": f"tournament-{t}", "updated_at": BASE_TS})
        teams = [t * 1000 + k for k in range(TEAMS_PER_TOURNAMENT)]
        for team in teams:
            participants.append({"id": team, "sport": sport, "name": f"Team {team} United",
                                 "name_folded": f"team {team} united", "updated_at": BASE_TS})
        for s in range(SEASONS_PER_TOURNAMENT):
            season_id = t * 10 + s
            seasons.append({"id": season_id, "tournament_id": t, "name": f"Season {season_id}",
                            "year": f"{24 + s}/{25 + s}", "sort_key": 2024.0 + s, "listed": 1, "position": s,
                            "updated_at": BASE_TS})
            for n in range(EVENTS_PER_SEASON):
                event_id += 1
                start = BASE_TS + s * 120 * 86400 + n * 86400 + t * 600
                status = _status(event_id)
                has_payload = 1 if event_id % 10 < 7 else 0
                observed = start + (event_id % 200) * 3600 if has_payload and event_id % 5 else None
                home, away = teams[n % TEAMS_PER_TOURNAMENT], teams[(n + 7) % TEAMS_PER_TOURNAMENT]
                events.append({
                    "id": event_id, "sport": sport, "category_id": t % 7, "tournament_id": t,
                    "season_id": season_id, "round": n // 10 + 1, "start_ts": start, "status_class": status,
                    "home_id": home, "away_id": away, "home_name": f"Team {home} United",
                    "away_name": f"Team {away} United", "home_score": n % 5, "away_score": n % 3,
                    "observed_at": observed, "observed_gap": observed - start if observed is not None else None,
                    "stale": 1 if event_id % 100 == 0 else 0,
                    "row_source": "event" if has_payload else "listing", "has_event_payload": has_payload,
                    "layout": ("legacy" if event_id % 10 == 0 else "v3") if has_payload else None,
                    "path": f"match_details/{t}/{season_id}/{event_id}" if has_payload and event_id % 10 == 0 else None,
                    "first_seen_at": start, "updated_at": start + (event_id % 500) * 60,
                })
                event_participants.append({"participant_id": home, "start_ts": start, "event_id": event_id, "side": 1})
                event_participants.append({"participant_id": away, "start_ts": start, "event_id": event_id, "side": 2})
                if has_payload:
                    for k, key in enumerate(SLICE_KEYS):
                        mark = (event_id + k) % 50
                        state = "error" if mark < 2 else "empty" if mark < 5 else "ok"
                        slices.append({
                            "event_id": event_id, "key": key, "state": state, "has_payload": 1 if state == "ok" else 0,
                            "fetched_at": start, "checked_at": start,
                            "empty_count": 1 if state == "empty" else 0,
                            "error_reason": "403" if state == "error" else None,
                            "stored_bytes": 1400, "raw_bytes": 9000,
                        })
                if event_id % 15 == 0:
                    changes.append({"seq": len(changes) + 1, "ts": start + 7200, "event_id": event_id, "sport": sport,
                                    "tournament_id": t, "fields": "status.code", "row_json": "{}",
                                    "segment": "changes/2026-10.jsonl"})
    return {"tournaments": tournaments, "seasons": seasons, "participants": participants, "events": events,
            "event_participants": event_participants, "event_slices": slices, "changes": changes}


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("plans")
    state_path = str(data_dir / "state.db")
    state = StateDb(state_path)  # şema göç betiğinden gelir; ANALYZE çalıştırılmaz
    try:
        with state.write() as writer:
            writer.executemany(
                "INSERT INTO follows (kind, entity_id, sport, name, enabled, position, created_at, updated_at) "
                "VALUES ('tournament', ?, ?, ?, ?, ?, 1, 1)",
                [(t, SPORTS[t % 4], f"Tournament {t}", 1 if t % 3 else 0, t) for t in range(1, 16)])
            writer.executemany(
                "INSERT INTO stream_events (stream, ts_ms, type, event_id, payload_json) VALUES (?, ?, ?, ?, '{}')",
                [(("live", "change", "job", "system")[n % 4], n, "x", n % 300) for n in range(4000)])
    finally:
        state.close()

    with Catalog(catalog.catalog_path(data_dir), attach={"state": state_path}) as cat:
        cat.prepare()
        with cat.write() as connection:
            for table, rows in _rows().items():
                cat.upsert(table, rows)
            cat.stamp_derive_version()
        connection.execute("ANALYZE main")
        assert not connection.execute("SELECT 1 FROM state.sqlite_master WHERE name LIKE 'sqlite_stat%'").fetchall()
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 7200
        assert connection.execute("SELECT count(*) FROM event_slices").fetchone()[0] == 30240
        yield connection


def plan(connection: sqlite3.Connection, sql: str, params=()) -> list[str]:
    """`EXPLAIN QUERY PLAN` çıktısının `detail` sütunu, satır satır."""
    return [str(row[3]) for row in connection.execute("EXPLAIN QUERY PLAN " + sql, params)]


def _reads(line: str, table: str) -> bool:
    # "SEARCH e USING ..." (3.36+) ve "SEARCH TABLE events AS e USING ..." (daha eski) biçimlerinin ikisi de
    return f" {table} " in f" {line} "


def uses(lines: list[str], table: str, index: str) -> bool:
    """Planda `table` (ad ya da takma ad) bu dizinle mi okunuyor: ... USING [COVERING] INDEX <index>."""
    return any(_reads(line, table) and re.search(rf"INDEX {re.escape(index)}\b", line) for line in lines)


def by_primary_key(lines: list[str], table: str) -> bool:
    """Planda `table` birincil anahtar aramasıyla mı okunuyor."""
    return any(_reads(line, table) and line.startswith("SEARCH") and "PRIMARY KEY" in line for line in lines)


MISSING = """
WITH req(sport, key) AS (VALUES ('football','statistics'), ('football','lineups'), ('basketball','statistics'),
                                ('', 'incidents'))
SELECT e.id, e.sport, e.has_event_payload, group_concat(r.key)
FROM events e
JOIN req r ON r.sport = e.sport OR r.sport = ''
LEFT JOIN event_slices s ON s.event_id = e.id AND s.key = r.key AND s.sub = ''
WHERE {scope}
  e.status_class IN ('completed', 'decided_without_play')
  AND (e.has_event_payload = 0
       OR s.event_id IS NULL
       OR (s.state != 'ok' AND s.empty_count + s.unverified_empty_count < :threshold))
GROUP BY e.id
"""

# (ad, sorgu, parametreler, [(tablo, beklenen dizin)]): bölüm 3.7'deki tablonun adlı dizin kullanan satırları,
# aynı sırayla. "Primary key" ve "table scan" denen satırlar aşağıda ayrı testlerdedir.
HOT = [
    ("season-newest-first",
     "SELECT * FROM events WHERE tournament_id = ? AND season_id = ? ORDER BY start_ts DESC, id DESC LIMIT 25",
     (17, 171), [("events", "events_tournament_season")]),
    ("season-next-page-keyset",
     "SELECT * FROM events WHERE tournament_id = ? AND season_id = ? AND (start_ts, id) < (?, ?) "
     "ORDER BY start_ts DESC, id DESC LIMIT 25",
     (17, 171, NOW, 10_000_500), [("events", "events_tournament_season")]),
    ("all-newest-first",
     "SELECT id, start_ts FROM events ORDER BY start_ts DESC, id DESC LIMIT 25",
     (), [("events", "events_start")]),
    ("all-newest-first-offset",
     "SELECT id, start_ts FROM events ORDER BY start_ts DESC, id DESC LIMIT 25 OFFSET 5000",
     (), [("events", "events_start")]),
    ("sport-date-range-status",
     "SELECT * FROM events WHERE sport = ? AND start_ts BETWEEN ? AND ? "
     "AND status_class IN ('completed', 'decided_without_play') ORDER BY start_ts DESC, id DESC LIMIT 50",
     ("football", BASE_TS, BASE_TS + 30 * 86400), [("events", "events_sport_start")]),
    ("several-tournaments-finished",
     "SELECT * FROM events WHERE tournament_id IN (?, ?, ?) AND status_class IN ('completed', 'decided_without_play') "
     "ORDER BY start_ts DESC, id DESC LIMIT 50",
     (3, 17, 29), [("events", "events_tournament_season")]),
    ("team-name-search",
     "SELECT e.* FROM participants p JOIN event_participants ep ON ep.participant_id = p.id "
     "JOIN events e ON e.id = ep.event_id WHERE p.name_folded LIKE ? ORDER BY ep.start_ts DESC LIMIT 50",
     ("%17005 uni%",), [("p", "participants_name")]),
    ("events-of-one-participant",
     "SELECT event_id FROM event_participants WHERE participant_id = ? ORDER BY start_ts DESC LIMIT 50",
     (17005,), []),
    ("followed-tournaments",
     "SELECT e.* FROM state.follows f JOIN events e ON e.tournament_id = f.entity_id "
     "WHERE f.kind = 'tournament' AND f.enabled = 1",
     (), [("e", "events_tournament_season")]),
    ("dashboard-one-tournament",
     "SELECT count(*), sum(has_event_payload) FROM events WHERE tournament_id = ?",
     (17,), [("events", "events_tournament_season")]),
    ("dashboard-all-tournaments",
     "SELECT tournament_id, count(*), sum(has_event_payload) FROM events GROUP BY tournament_id",
     (), [("events", "events_tournament_season")]),
    ("missing-one-season",
     MISSING.format(scope="e.tournament_id = :t AND e.season_id = :s AND"),
     {"t": 17, "s": 171, "threshold": 2}, [("e", "events_tournament_season")]),
    ("slices-in-error-for-one-key",
     "SELECT event_id FROM event_slices WHERE state != 'ok' AND state = 'error' AND key = ?",
     ("statistics",), [("event_slices", "event_slices_not_ok")]),
    ("refresh-candidates",
     "SELECT id FROM events WHERE has_event_payload = 1 AND observed_at IS NOT NULL "
     "AND observed_gap < ? AND observed_at <= ?",
     (72 * 3600, NOW - 6 * 3600), [("events", "events_unsettled")]),
    ("refresh-candidates-with-status",
     "SELECT id FROM events WHERE has_event_payload = 1 AND observed_at IS NOT NULL "
     "AND observed_gap < ? AND observed_at <= ? AND status_class IN ('completed', 'decided_without_play', 'void')",
     (72 * 3600, NOW - 6 * 3600), [("events", "events_unsettled")]),
    ("live-events-of-a-sport",
     "SELECT sport, start_ts FROM events WHERE status_class = 'live' AND sport = ?",
     ("tennis",), [("events", "events_live")]),
    ("export-scan-batch",
     "SELECT * FROM events WHERE sport = ? AND (start_ts, id) > (?, ?) ORDER BY start_ts, id LIMIT 1000",
     ("football", BASE_TS, 0), [("events", "events_sport_start")]),
    ("migration-work-list",
     "SELECT id FROM events WHERE layout = 'legacy' ORDER BY id",
     (), [("events", "events_legacy")]),
    ("changed-since",
     "SELECT id, updated_at FROM events WHERE updated_at > ? ORDER BY updated_at, id LIMIT 1000",
     (BASE_TS + 86400,), [("events", "events_updated")]),
    ("events-that-should-have-started",
     "SELECT id FROM events WHERE status_class IN ('not_started', 'live', 'unknown') AND start_ts <= ?",
     (NOW,), [("events", "events_open")]),
    ("stream-read",
     "SELECT * FROM state.stream_events WHERE stream = ? AND seq > ? ORDER BY seq LIMIT 500",
     ("live", 100), [("state.stream_events", "stream_events_stream")]),
]

# Bölüm 3.7'nin dışında kalan ama şemadaki bir dizinin var olma nedeni olan sorgular
OTHER = [
    ("refresh-legacy-unobserved",  # bölüm 8.4: include_unobserved
     "SELECT id FROM events WHERE has_event_payload = 1 AND observed_at IS NULL AND tournament_id = ?",
     (17,), [("events", "events_unobserved")]),
    ("stale-rows",  # bölüm 8.2: EventStore.stale()
     "SELECT id FROM events WHERE stale = 1",
     (), [("events", "events_stale")]),
    ("superseded-legacy-directories",
     "SELECT id FROM events WHERE legacy_path IS NOT NULL ORDER BY id",
     (), [("events", "events_legacy")]),
    ("round-of-a-season",
     "SELECT * FROM events WHERE season_id = ? AND round = ? ORDER BY start_ts",
     (171, 3), [("events", "events_season_round")]),
    ("seasons-of-a-tournament-newest-first",
     "SELECT * FROM seasons WHERE tournament_id = ? ORDER BY sort_key DESC, id DESC",
     (17,), [("seasons", "seasons_tournament")]),
    ("tournaments-of-a-sport",
     "SELECT * FROM tournaments WHERE sport = ? ORDER BY name_folded",
     ("football",), [("tournaments", "tournaments_sport")]),
    ("player-name-prefix",
     "SELECT id FROM players WHERE name_folded >= ? AND name_folded < ?",
     ("mes", "met"), [("players", "players_name")]),
    ("participants-of-an-event",
     "SELECT participant_id, side FROM event_participants WHERE event_id = ?",
     (10_000_500,), [("event_participants", "event_participants_event")]),
    ("changes-of-an-event",
     "SELECT * FROM changes WHERE event_id = ? AND seq > ? ORDER BY seq",
     (10_000_500, 0), [("changes", "changes_event")]),
    ("changes-since-a-time",
     "SELECT * FROM changes WHERE ts >= ? ORDER BY ts",
     (BASE_TS,), [("changes", "changes_ts")]),
]


QUERIES = {query[0]: query for query in HOT + OTHER}
assert len(QUERIES) == len(HOT) + len(OTHER)


def _query(name: str):
    _, sql, params, _ = QUERIES[name]
    return sql, params


@pytest.mark.parametrize("name, sql, params, expected", HOT + OTHER, ids=[q[0] for q in HOT + OTHER])
def test_query_uses_the_named_index(conn, name, sql, params, expected):
    lines = plan(conn, sql, params)

    for table, index in expected:
        assert uses(lines, table, index), f"{name}: {index} planda yok:\n" + "\n".join(lines)
    conn.execute(sql, params).fetchmany(5)  # sorgu gerçekten çalışıyor


def test_every_index_of_the_schema_is_exercised_by_a_pinned_query(conn):
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM main.sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'")}
    pinned = {index for query in HOT + OTHER for _, index in query[3]}

    assert indexes - pinned == set()
    assert pinned - indexes == {"stream_events_stream"}  # state.db'nin dizini


def test_sorted_listings_need_no_sort_step(conn):
    """Sayfalama sorguları sırayı dizinden alır: planda geçici B-ağacı (sıralama) yok."""
    for name in ("season-newest-first", "season-next-page-keyset", "all-newest-first", "all-newest-first-offset",
                 "export-scan-batch", "changed-since", "stream-read", "migration-work-list"):
        lines = plan(conn, *_query(name))
        assert not any("TEMP B-TREE" in line for line in lines), f"{name}:\n" + "\n".join(lines)


def test_primary_key_lookups(conn):
    """Bölüm 3.7'de "primary key" denen satırlar: adlı dizin yok, birincil anahtar araması var."""
    one_participant = plan(conn, "SELECT event_id FROM event_participants WHERE participant_id = ? "
                                 "ORDER BY start_ts DESC LIMIT 50", (17005,))
    assert by_primary_key(one_participant, "event_participants")
    assert not any("TEMP B-TREE" in line for line in one_participant)

    one_event = plan(conn, "SELECT * FROM event_slices WHERE event_id = ?", (10_000_500,))
    assert by_primary_key(one_event, "event_slices")

    batch = plan(conn, "SELECT * FROM event_slices WHERE event_id BETWEEN ? AND ? AND has_payload = 1",
                 (10_000_000, 10_001_000))
    assert by_primary_key(batch, "event_slices")

    search = plan(conn, *_query("team-name-search"))
    assert by_primary_key(search, "ep") and by_primary_key(search, "e")


def test_missing_query_reads_slices_by_primary_key(conn):
    """ "Ne eksik" sorgusu: olaylar dizinden (ya da tümü taranarak), dilimler (olay, anahtar) aramasıyla."""
    one_season = plan(conn, MISSING.format(scope="e.tournament_id = :t AND e.season_id = :s AND"),
                      {"t": 17, "s": 171, "threshold": 2})
    whole = plan(conn, MISSING.format(scope=""), {"threshold": 2})

    assert uses(one_season, "e", "events_tournament_season") and by_primary_key(one_season, "s")
    assert by_primary_key(whole, "s")
    rows = conn.execute(MISSING.format(scope="e.tournament_id = :t AND e.season_id = :s AND"),
                        {"t": 17, "s": 171, "threshold": 2}).fetchall()
    assert rows and all(r[3] for r in rows)


def test_spelling_event_slices_not_ok_needs_the_literal_condition(conn):
    """
    Bölüm 3.7: `event_slices_not_ok` dizinini isteyen sorgu `state != 'ok'` koşulunu açıkça yazmalı;
    planlayıcı bunu `state = 'error'`dan çıkaramaz.
    """
    key = ("statistics",)

    for sql in (
        "SELECT event_id FROM event_slices WHERE state != 'ok' AND state = 'error' AND key = ?",
        "SELECT event_id FROM event_slices WHERE state != 'ok' AND state IN ('error', 'empty') AND key = ?",
    ):
        assert uses(plan(conn, sql, key), "event_slices", "event_slices_not_ok"), sql
    assert uses(plan(conn, "SELECT event_id, key FROM event_slices WHERE state != 'ok'"),
                "event_slices", "event_slices_not_ok")

    without = "SELECT event_id FROM event_slices WHERE state = 'error' AND key = ?"
    assert not uses(plan(conn, without, key), "event_slices", "event_slices_not_ok")


def test_spelling_refresh_query_repeats_both_conditions_of_events_unsettled(conn):
    """Bölüm 3.7: yenileme sorgusu `events_unsettled` dizininin iki koşulunu da yinelemeli."""
    window = (72 * 3600,)
    full = "SELECT id FROM events WHERE has_event_payload = 1 AND observed_at IS NOT NULL AND observed_gap < ?"

    assert uses(plan(conn, full, window), "events", "events_unsettled")
    assert uses(plan(conn, full + " AND observed_at <= ?", (72 * 3600, NOW)), "events", "events_unsettled")
    for partial in (
        "SELECT id FROM events WHERE observed_gap < ?",
        "SELECT id FROM events WHERE has_event_payload = 1 AND observed_gap < ?",
        "SELECT id FROM events WHERE observed_at IS NOT NULL AND observed_gap < ?",
    ):
        assert not uses(plan(conn, partial, window), "events", "events_unsettled"), partial


def test_spelling_events_open_needs_the_three_classes_in_the_order_of_the_index(conn):
    """
    Tasarımda yazmayan üçüncü kural: `events_open` yalnızca IN listesi dizindeki üç sabitle ve aynı sırayla
    yazılırsa kullanılır. Sırası değişmiş, alt küme ya da parametreli liste dizine ulaşamaz; daha dar bir
    süzgeç gerekiyorsa üçlü liste yazılır ve yanına ek koşul konur.
    """
    exact = "SELECT id FROM events WHERE status_class IN ('not_started', 'live', 'unknown') AND start_ts <= ?"

    assert uses(plan(conn, exact, (NOW,)), "events", "events_open")
    assert uses(plan(conn, exact + " AND status_class != 'unknown'", (NOW,)), "events", "events_open")
    for sql, params in (
        ("SELECT id FROM events WHERE status_class IN ('live', 'not_started', 'unknown') AND start_ts <= ?", (NOW,)),
        ("SELECT id FROM events WHERE status_class IN ('not_started', 'live') AND start_ts <= ?", (NOW,)),
        ("SELECT id FROM events WHERE status_class IN (?, ?, ?) AND start_ts <= ?",
         ("not_started", "live", "unknown", NOW)),
    ):
        assert not uses(plan(conn, sql, params), "events", "events_open"), sql


def test_spelling_other_partial_indexes(conn):
    """`events_live`, `events_legacy`, `events_stale`, `events_unobserved`: dizinin koşulu sabitle yazılır."""
    assert uses(plan(conn, "SELECT id FROM events WHERE status_class = 'live' AND sport = ?", ("tennis",)),
                "events", "events_live")
    assert not uses(plan(conn, "SELECT id FROM events WHERE status_class IN ('live') AND sport = ?", ("tennis",)),
                    "events", "events_live")
    assert uses(plan(conn, "SELECT id FROM events WHERE (layout = 'legacy' OR legacy_path IS NOT NULL) ORDER BY id"),
                "events", "events_legacy")
    assert uses(plan(conn, "SELECT id FROM events WHERE has_event_payload = 1 AND observed_at IS NULL"),
                "events", "events_unobserved")
    assert not uses(plan(conn, "SELECT id FROM events WHERE observed_at IS NULL"), "events", "events_unobserved")


def test_results_of_the_pinned_queries_are_right(conn):
    """Dizin seçimi sonucu değiştirmez: birkaç sorgu, dizinsiz hesaplanan beklenen değerle karşılaştırılır."""
    events = [dict(r) for r in conn.execute("SELECT * FROM events NOT INDEXED")]

    season = sorted((e for e in events if e["tournament_id"] == 17 and e["season_id"] == 171),
                    key=lambda e: (e["start_ts"], e["id"]), reverse=True)
    first_page, _ = _query("season-newest-first")
    next_page, _ = _query("season-next-page-keyset")
    assert [r["id"] for r in conn.execute(first_page, (17, 171))] == [e["id"] for e in season[:25]]
    last = season[24]
    assert [r["id"] for r in conn.execute(next_page, (17, 171, last["start_ts"], last["id"]))] == \
        [e["id"] for e in season[25:50]]

    window, cutoff = 72 * 3600, NOW - 6 * 3600
    due = {e["id"] for e in events if e["has_event_payload"] == 1 and e["observed_at"] is not None
           and e["observed_gap"] < window and e["observed_at"] <= cutoff}
    assert {r[0] for r in conn.execute(*_query("refresh-candidates"))} == due and due

    live = {(e["sport"], e["start_ts"]) for e in events if e["status_class"] == "live" and e["sport"] == "tennis"}
    assert set(map(tuple, conn.execute(*_query("live-events-of-a-sport")))) == live and live

    legacy = sorted(e["id"] for e in events if e["layout"] == "legacy")
    assert [r[0] for r in conn.execute(*_query("migration-work-list"))] == legacy and legacy

    opened = {e["id"] for e in events
              if e["status_class"] in ("not_started", "live", "unknown") and e["start_ts"] <= NOW}
    assert {r[0] for r in conn.execute(*_query("events-that-should-have-started"))} == opened and opened

    followed = {e["id"] for e in events if e["tournament_id"] <= 15 and e["tournament_id"] % 3}
    assert {r["id"] for r in conn.execute(*_query("followed-tournaments"))} == followed and followed

    not_ok = {(r[0], r[1]) for r in conn.execute("SELECT event_id, state FROM event_slices NOT INDEXED "
                                                 "WHERE key = 'statistics' AND state = 'error'")}
    assert {r[0] for r in conn.execute(*_query("slices-in-error-for-one-key"))} == {e for e, _ in not_ok} and not_ok
