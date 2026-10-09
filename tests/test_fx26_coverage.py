"""
Takiplerin kapsamı ve tamlığı yalnızca bitmiş maçları sayar (FX-26; canlı doğrulama M12, M12b).

EHF Şampiyonlar Ligi'nin ilk indirmesinden sonra sezon satırı "72 maç · 29 bitti · 43 ayrıntılı · tüm verisi
inen %67 · eksik: İstatistikler (14) …" diyordu: eksik 14 maç henüz oynanmamıştı. Burada:

  * `season_counts`: tamlık (`complete`, `missing`, `completion_rate`) yalnızca bitmiş ve detayı saklanan maçlardan
    (`finished_details`); detayı saklanan gelecek fikstür ne eksik ne tam sayılır;
  * `/status` özeti turnuva başına `finished_details` verir: ön yüz bir ligin kapsamını `finished_details /
    finished` olarak gösterir, takım ve maç takibininkini de aynı kuralla sayar.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from fastapi.testclient import TestClient

import conftest
import store_fixtures as sf
from sofascore_scraper.services import planning
from sofascore_scraper.services import status as status_module
from sofascore_scraper.services.status import StatusService
from sofascore_scraper.slices import SLICE_OK, Outcome
from sofascore_scraper.store import FollowSpec, Store, open_store
from sofascore_scraper.web.app import app

client = TestClient(app)
FINISHED = "football/A_finished-100-ended__16837335"
NOT_STARTED = "football/A_notstarted-0-not-started__17184998"
REQUIRED = planning.expected_slice_keys("football")


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Store]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conftest.STORE_BOUNDARY.add_data_dir(str(data_dir))
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(data_dir))
    monkeypatch.delenv("SOFASCORE_FETCH__ONLY_FINISHED", raising=False)
    status_module.forget_sizes()
    yield open_store(data_dir)
    status_module.forget_sizes()


def put(store: Store, eid: int, case: str, *, complete: bool) -> None:
    payload: Dict[str, Any] = sf.basic_payload(sf.Ev(case, sf.PL, sf.PL_2526, f"Home {eid}", f"Away {eid}", eid=eid))
    outcomes: Dict[str, Outcome] = {"event": Outcome(SLICE_OK, data=payload)}
    if complete:
        outcomes.update({key: Outcome(SLICE_OK, data=sf.slice_payload(key, payload)) for key in REQUIRED})
    store.events.put(eid, outcomes)


def league(store: Store) -> None:
    """Üç bitmiş maç (ikisi tam), iki detayı saklanan gelecek maç (hiç dilimi yok)."""
    put(store, 9500001, FINISHED, complete=True)
    put(store, 9500002, FINISHED, complete=True)
    put(store, 9500003, FINISHED, complete=False)
    put(store, 9500004, NOT_STARTED, complete=False)
    put(store, 9500005, NOT_STARTED, complete=False)


def test_season_completeness_counts_finished_matches_only(store: Store) -> None:
    league(store)
    [season] = [c for c in StatusService(store).season_counts(sf.PL.id) if c.season_id == sf.PL_2526.id]
    assert (season.events, season.finished, season.details, season.finished_details) == (5, 3, 5, 3)
    assert season.complete == 2
    assert season.completion_rate == round(2 / 3 * 100, 2)
    # yalnızca bitmiş ama eksik maç: her beklenen dilim için 1 (gelecek iki maç sayılmaz)
    assert season.missing and set(season.missing.values()) == {1}


def test_the_api_season_counts_and_the_status_summary(store: Store) -> None:
    league(store)
    store.follows.add(FollowSpec(kind="tournament", entity_id=sf.PL.id, name="Premier League"))
    seasons = client.get(f"/api/v1/tournaments/{sf.PL.id}/seasons", params={"include": "counts"}).json()["data"]
    counts = next(s["counts"] for s in seasons if s["id"] == sf.PL_2526.id)
    assert (counts["finished"], counts["details"], counts["finished_details"], counts["complete"]) == (3, 5, 3, 2)
    assert counts["completion_rate"] == 66.67
    assert all(n == 1 for n in counts["missing"].values())
    summary = client.get("/api/v1/status").json()["data"]["summary"]
    pl = next(t for t in summary["tournaments"] if t["tournament_id"] == sf.PL.id)
    assert (pl["finished"], pl["details"], pl["finished_details"]) == (3, 5, 3)
    # eski alanlar değişmedi: `matches` ayarın kuralıyla (bitmiş + detaylı bitmemiş), `coverage` details / matches
    assert (pl["matches"], pl["coverage"]) == (5, 100.0)


def test_only_unfinished_details_give_zero_finished_details(store: Store) -> None:
    put(store, 9500004, NOT_STARTED, complete=False)
    [row] = [t for t in StatusService(store).summary().tournaments if t.tournament_id == sf.PL.id]
    assert (row.details, row.finished, row.finished_details) == (1, 0, 0)
