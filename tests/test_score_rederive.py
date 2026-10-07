"""
F10 (FX-23): uzatmasız doğrudan penaltıya giden futbol maçının kayıtlı skoru nasıl düzelir.

Skor çizelgesi (`scores_json`) katalogda saklanır ve yükten türetilir (sofascore_scraper/store/derive.py). Kural
değişince DERIVE_VERSION arttı; eski sürümle yazılmış katalog ilk açılışta dosyalardan yeniden kurulur. Bu test o
yolu sınar: eski kuralla yazılmış satır (aet dolu, sürüm 5) depo açılınca düzelir, kullanıcının bir şey
çalıştırması gerekmez.
"""
from __future__ import annotations

import json
from pathlib import Path

import store_fixtures as sf
from sofascore_scraper.schema import mappers
from sofascore_scraper.store import open_store
from sofascore_scraper.store.derive import DERIVE_VERSION

CUP_PEN = sf.event_id(sf.CUP_PEN)  # kod 120, uzatma anahtarı yok: doğrudan penaltılar
CUP_PEN_2 = sf.event_id(sf.CUP_PEN_2)  # kod 120, uzatma 0-0 oynandı, sonra penaltılar


def _sheet(store, event_id: int) -> dict:
    return json.loads(store.events.get(event_id).scores_json)


def test_a_catalog_of_the_old_rule_is_rebuilt_and_loses_the_false_extra_time(tmp_path: Path) -> None:
    data = sf.build_fixture("canonical", tmp_path / "data").data_dir
    first = open_store(data)
    assert _sheet(first, CUP_PEN)["aet"] is None
    assert _sheet(first, CUP_PEN_2)["aet"] == [1, 1]
    # 3.0.0'ın eski kuralıyla yazılmış katalog: sürüm 5, uzatma skoru penaltılı maçta dolu
    stale = dict(_sheet(first, CUP_PEN), aet=[3, 3])
    with first._catalog.write() as conn:
        conn.execute("UPDATE meta SET value = '5' WHERE key = 'derive_version'")
        conn.execute("UPDATE events SET scores_json = ? WHERE id = ?", (json.dumps(stale), CUP_PEN))
    first.close()

    store = open_store(data)

    assert DERIVE_VERSION > 5
    assert store._catalog.get_meta("derive_version") == str(DERIVE_VERSION)
    assert _sheet(store, CUP_PEN)["aet"] is None
    score = mappers.score_from_row(store.events.get(CUP_PEN)).to_dict()
    assert score["after_extra_time"] is None
    assert score["regulation"] == {"home": 3, "away": 3}
    assert score["penalties"] == {"home": 7, "away": 6}
    assert mappers.score_from_row(store.events.get(CUP_PEN_2)).to_dict()["after_extra_time"] == {"home": 1, "away": 1}
    store.close()
