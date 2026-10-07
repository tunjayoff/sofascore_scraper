"""
`ssc status`ın FX-13 eklemeleri (plan maddesi FX-13; docs/design/05-web-ui.md G20, G21, G22; ST-23 ve ST-24'ün
notları). Tümü çevrimdışı; veri dizini `tests/store_fixtures.py`'nin `canonical` dizinidir.

  * son taşıma (`migration_runs`), hiç taşıma yoksa null;
  * sink konumlarının gecikmesi (`lag_events`: günlüğün son sıra numarası eksi konum) ve metindeki satırı;
  * `--disk`: disk kullanımı, v3 ağacı toplamda; verilmezse klasör gezilmez (`disk` alanı yok).
"""
from __future__ import annotations

from pathlib import Path

import store_fixtures as sf
import test_cli_skeleton as skeleton
from sofascore_scraper.store import StreamEvent, open_store
from test_cli_skeleton import CliRunner

cli = skeleton.cli


def test_status_shows_the_last_migration_the_sink_lag_and_the_disk(cli: CliRunner, tmp_path: Path) -> None:
    data_dir = sf.build_fixture("canonical", tmp_path / "data").data_dir
    store = open_store(data_dir)
    before = cli("--data-dir", str(data_dir), "status", "--json")
    assert before.exit_code == 0, before.stderr
    assert before.data["last_migration"] is None and before.data["sinks"] == [] and "disk" not in before.data

    report = store.migrate.run()
    seqs = store.streams.append("live", [StreamEvent(type="live.score_changed", data={"n": i}, ts=1000.0 + i)
                                         for i in range(3)])
    store.streams.set_cursor("hook", seqs[0])
    head = store.streams.head().last_seq

    run = cli("--data-dir", str(data_dir), "status", "--json", "--disk")
    assert run.exit_code == 0, run.stderr
    migration = run.data["last_migration"]
    assert migration["id"] == report.run_id and migration["finished_at"].endswith("Z")
    assert migration["events_done"] > 0 and migration["events_failed"] == 0
    (sink,) = run.data["sinks"]
    assert (sink["sink"], sink["seq"], sink["lag_events"]) == ("hook", seqs[0], head - seqs[0])
    disk = run.data["disk"]
    assert disk["v3"] == disk["entries"]["v3"] > 0
    assert disk["total"] == sum(disk[k] for k in ("seasons", "matches", "details", "datasets", "v3", "changes"))

    text = cli("--data-dir", str(data_dir), "status", "--disk").stdout
    assert f"Last migration: run {report.run_id}, {migration['events_done']} events moved, 0 failed" in text
    assert f"Sink hook: {head - seqs[0]} events behind" in text
    assert "Disk: " in text and "in the 3.0 layout" in text
