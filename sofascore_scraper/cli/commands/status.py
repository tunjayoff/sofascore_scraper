"""
`status`: veri dizininin durumu (docs/design/02-services.md bölüm 4.1; plan maddesi P19). Yalnızca okur; kilit
almaz, SofaScore'a istek atmaz.

    ssc status                 veri özeti, depo sürümleri, kilitler, çalışan ve son iş, canlı servis, sink'ler
                               ve gecikmeleri, son taşıma
    ssc status --coverage      ayrıca turnuva başına maç ve detay sayıları
    ssc status --disk          ayrıca veri klasörünün disk kullanımı (klasörü gezer; v3 ağacı dahil, FX-13)
    ssc status --check         yalnızca çıkış kodu: 0 sağlıklı, 1 sağlıksız (katalog yeniden kurulmalı)

  * Henüz depo olmayan bir veri dizini (hiçbir komut çalışmamış) boş bir durumdur, hata değildir; hiçbir şey
    oluşturulmaz.
  * Canlı servisin alanları `services.live.live_status`'tandır: kilit sahibi, kaynak, öndeki kaynak (spor
    başına), son kaynak değişimi, son kalp atışı.
  * Sink'lerin konumu ve son hatası olay günlüğünün sink imleçlerindendir; gecikme (`lag_events`) günlüğün son
    sıra numarası eksi konumdur (plan maddesi FX-13).
  * Son taşıma (`ssc migrate`) `migration_runs`ın son gerçek çalışmasıdır (`Migrator.last_run`, FX-13).

Ağır içe aktarmalar (servisler, Store) işlevin içindedir.
"""
from __future__ import annotations

import argparse
import dataclasses
from typing import Any, Dict, List, Optional

from sofascore_scraper.cli import exit_codes
from sofascore_scraper.cli.commands import CommandResult, Invocation, command
from sofascore_scraper.cli.commands.jobs import job_dict, job_line
from sofascore_scraper.cli.commands.sync import data_dir_of_settings
from sofascore_scraper.cli.output import Translator


def format_size(size: float) -> str:
    """Bayt sayısı okunur biçimde (1024'lük birimler, dil bağımsız): 0.0 B, 6.6 MB (`ssc status --disk`)."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--check", action="store_true", help=t("ssc_help_status_check"))
    parser.add_argument("--coverage", action="store_true", help=t("ssc_help_status_coverage"))
    parser.add_argument("--disk", action="store_true", help=t("ssc_help_status_disk"))


def _open(data_dir: str) -> Optional[Any]:
    from sofascore_scraper.store import StoreError, open_store

    try:
        return open_store(data_dir, create=False)
    except StoreError as e:
        if type(e) is StoreError:
            return None
        raise


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    import datetime as _dt

    return _dt.datetime.fromtimestamp(float(epoch), tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _leases(info: Any) -> List[Dict[str, Any]]:
    return [{"name": lease.name, "pid": lease.pid, "host": lease.host, "purpose": lease.purpose or None,
             "since": _iso(lease.acquired_at)} for lease in info.leases]


def _sinks(store: Any) -> List[Dict[str, Any]]:
    try:
        cursors = store.streams.cursors()
        head = store.streams.head().last_seq
    except Exception:  # olay günlüğü okunamıyor: durumun geri kalanı yine verilir
        return []
    return [dict(dataclasses.asdict(cursor), lag_events=max(0, head - cursor.seq)) for cursor in cursors]


def _migration(store: Any) -> Optional[Dict[str, Any]]:
    run = store.migrate.last_run()
    if run is None:
        return None
    return dict(run, started_at=_iso(run["started_at"]), finished_at=_iso(run["finished_at"]))


def _disk(summary: Any) -> Optional[Dict[str, Any]]:
    disk = summary.disk
    if disk is None:
        return None
    return {"entries": dict(disk.entries), "seasons": disk.seasons, "matches": disk.matches, "details": disk.details,
            "datasets": disk.datasets, "v3": disk.v3, "changes": disk.changes, "total": disk.total}


def _jobs(store: Any) -> Dict[str, Any]:
    from sofascore_scraper.jobs.manager import JobManager
    from sofascore_scraper.store import JobStore

    manager = JobManager(JobStore.for_store(store))
    running = manager.active()
    latest = manager.list(limit=1)
    return {"running": job_dict(running) if running is not None else None,
            "last": job_dict(latest[0]) if latest else None,
            "_running": running, "_last": latest[0] if latest else None}


def _live(store: Any) -> Optional[Dict[str, Any]]:
    from sofascore_scraper.services.live.supervisor import live_status

    try:
        return live_status(store)
    except Exception:
        return None


@command("status", help="ssc_help_cmd_status", configure=_arguments, settings=True)
def status(inv: Invocation) -> CommandResult:
    from sofascore_scraper.services.status import StatusService

    t = inv.t
    data_dir = data_dir_of_settings()
    store = _open(data_dir)
    if store is None:
        data: Dict[str, Any] = {"data_dir": data_dir, "store": None, "healthy": True}
        return CommandResult(data=data, text=t("ssc_status_no_store", path=data_dir),
                             exit_code=exit_codes.OK)

    info = store.info(sizes=False)
    summary = StatusService(store).summary(sizes=bool(inv.args.disk))
    jobs = _jobs(store)
    live = _live(store)
    healthy = info.catalog_rebuild_reason is None
    data = {
        "data_dir": data_dir,
        "healthy": healthy,
        "store": {
            "store_id": info.store_id,
            "layout_version": info.layout_version,
            "state_schema": info.state_schema,
            "catalog_schema": info.catalog_schema,
            "derive_version": info.derive_version,
            "catalog_rebuild_reason": info.catalog_rebuild_reason,
            "last_rebuild": info.last_rebuild,
            "journal_modes": dict(info.journal_modes),
            "events_by_layout": dict(info.events_by_layout),
        },
        "data": {"matches": summary.matches, "details": summary.details, "seasons": summary.seasons,
                 "only_finished": summary.only_finished},
        "leases": _leases(info),
        "jobs": {"running": jobs["running"], "last": jobs["last"]},
        "live": live,
        "sinks": _sinks(store),
        "last_migration": _migration(store),
    }
    if inv.args.disk:
        data["disk"] = _disk(summary)
    if inv.args.coverage:
        data["coverage"] = [dict(dataclasses.asdict(row), coverage=row.coverage) for row in summary.tournaments]
    code = exit_codes.OK if healthy else exit_codes.GENERAL_ERROR
    if inv.args.check:
        return CommandResult(data={"healthy": healthy, "catalog_rebuild_reason": info.catalog_rebuild_reason},
                             exit_code=code)

    lines = [t("ssc_status_data_dir", path=data_dir)]
    if healthy:
        lines.append(t("ssc_status_store", layout=info.layout_version, state=info.state_schema,
                       catalog=info.catalog_schema if info.catalog_schema is not None else "-"))
    else:
        lines.append(t("ssc_status_unhealthy", reason=info.catalog_rebuild_reason))
    lines.append(t("ssc_status_data", matches=summary.matches, details=summary.details, seasons=summary.seasons))
    if data.get("disk"):
        lines.append(t("ssc_status_disk", total=format_size(data["disk"]["total"]), v3=format_size(data["disk"]["v3"])))
    migration = data["last_migration"]
    if migration is not None:
        lines.append(t("ssc_status_migration", id=migration["id"], done=migration["events_done"],
                       failed=migration["events_failed"], finished=migration["finished_at"])
                     if migration["finished_at"] else
                     t("ssc_status_migration_unfinished", id=migration["id"], started=migration["started_at"] or "?"))
    running, last = jobs["_running"], jobs["_last"]
    lines.append(t("ssc_status_running_job", job=job_line(t, running)) if running is not None
                 else t("ssc_status_no_running_job"))
    if last is not None and (running is None or last.id != running.id):
        lines.append(t("ssc_status_last_job", job=job_line(t, last)))
    if live is not None and live.get("running"):
        leaders = ", ".join(f"{sport}: {source}" for sport, source in sorted((live.get("leaders") or {}).items()))
        lines.append(t("ssc_status_live", pid=live.get("pid") or "?", source=live.get("source") or "?",
                       sports=", ".join(live.get("sports") or []) or "-", leaders=leaders or "-"))
    else:
        lines.append(t("ssc_status_live_off"))
    for lease in data["leases"]:
        lines.append(t("ssc_status_lease", name=lease["name"], pid=lease["pid"] or "?", host=lease["host"] or "?",
                       purpose=lease["purpose"] or "?", since=lease["since"] or "?"))
    for cursor in data["sinks"]:
        if cursor.get("last_error"):
            lines.append(t("ssc_status_sink_error", sink=cursor.get("sink") or "?", error=cursor["last_error"]))
        if cursor.get("lag_events"):
            lines.append(t("ssc_status_sink_lag", sink=cursor.get("sink") or "?", lag=cursor["lag_events"]))
    if inv.args.coverage:
        for row in summary.tournaments:
            lines.append(t("ssc_status_coverage", tournament=row.tournament_id if row.tournament_id is not None else "-",
                           matches=row.matches, details=row.details, coverage=row.coverage))
    return CommandResult(data=data, text="\n".join(lines))


__all__: List[str] = []
