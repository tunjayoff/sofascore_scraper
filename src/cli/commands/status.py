"""
`status`: veri dizininin durumu (docs/design/02-services.md bölüm 4.1; plan maddesi P19). Yalnızca okur; kilit
almaz, SofaScore'a istek atmaz.

    ssc status                 veri özeti, depo sürümleri, kilitler, çalışan ve son iş, canlı servis, sink'ler
    ssc status --coverage      ayrıca turnuva başına maç ve detay sayıları
    ssc status --check         yalnızca çıkış kodu: 0 sağlıklı, 1 sağlıksız (katalog yeniden kurulmalı)

  * Henüz depo olmayan bir veri dizini (hiçbir komut çalışmamış) boş bir durumdur, hata değildir; hiçbir şey
    oluşturulmaz.
  * Canlı servisin alanları `services.live.live_status`'tandır: kilit sahibi, kaynak, öndeki kaynak (spor
    başına), son kaynak değişimi, son kalp atışı.
  * Sink'lerin konumu ve son hatası olay günlüğünün sink imleçlerindendir.

Ağır içe aktarmalar (servisler, Store) işlevin içindedir.
"""
from __future__ import annotations

import argparse
import dataclasses
from typing import Any, Dict, List, Optional

from src.cli import exit_codes
from src.cli.commands import CommandResult, Invocation, command
from src.cli.commands.jobs import job_dict, job_line
from src.cli.commands.sync import data_dir_of_settings
from src.cli.output import Translator


def _arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--check", action="store_true", help=t("ssc_help_status_check"))
    parser.add_argument("--coverage", action="store_true", help=t("ssc_help_status_coverage"))


def _open(data_dir: str) -> Optional[Any]:
    from src.store import StoreError, open_store

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
    except Exception:  # olay günlüğü okunamıyor: durumun geri kalanı yine verilir
        return []
    return [dataclasses.asdict(cursor) for cursor in cursors]


def _jobs(store: Any) -> Dict[str, Any]:
    from src.jobs.manager import JobManager
    from src.store import JobStore

    manager = JobManager(JobStore.for_store(store))
    running = manager.active()
    latest = manager.list(limit=1)
    return {"running": job_dict(running) if running is not None else None,
            "last": job_dict(latest[0]) if latest else None,
            "_running": running, "_last": latest[0] if latest else None}


def _live(store: Any) -> Optional[Dict[str, Any]]:
    from src.services.live.supervisor import live_status

    try:
        return live_status(store)
    except Exception:
        return None


@command("status", help="ssc_help_cmd_status", configure=_arguments, settings=True)
def status(inv: Invocation) -> CommandResult:
    from src.services.status import StatusService

    t = inv.t
    data_dir = data_dir_of_settings()
    store = _open(data_dir)
    if store is None:
        data: Dict[str, Any] = {"data_dir": data_dir, "store": None, "healthy": True}
        return CommandResult(data=data, text=t("ssc_status_no_store", path=data_dir),
                             exit_code=exit_codes.OK)

    info = store.info(sizes=False)
    summary = StatusService(store).summary(sizes=False)
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
    }
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
    if inv.args.coverage:
        for row in summary.tournaments:
            lines.append(t("ssc_status_coverage", tournament=row.tournament_id if row.tournament_id is not None else "-",
                           matches=row.matches, details=row.details, coverage=row.coverage))
    return CommandResult(data=data, text="\n".join(lines))


__all__: List[str] = []
