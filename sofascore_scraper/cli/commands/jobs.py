"""
`jobs`: iş geçmişi ve denetimi, süreçler arasında (docs/design/02-services.md bölüm 2.8 ve 4.1; plan maddesi P19).

    ssc jobs list [--limit 20] [--kind sync] [--state running]
    ssc jobs show ID
    ssc jobs cancel ID                 işin iptalini ister; iş hangi süreçte (web, başka bir CLI) çalışırsa çalışsın
    ssc jobs tail ID [--follow]        işin olayları (NDJSON); --follow iş bitene kadar bekler

  * Okuma komutlarıdır: kilit almazlar. `cancel` iş satırına iptal bayrağını yazar; işi çalıştıran süreç onu en
    geç bir saniye içinde görür. Bitmiş bir işin iptali hata değildir (`cancelled: false`, çıkış kodu 0).
  * Bilinmeyen iş kimliği `not_found` (çıkış kodu 1).
  * `tail` akış komutudur: satır başına bir olay, `{"type":"job.<tür>","job_id","seq","ts","data"}`; akış
    kendiliğinden bittiğinde son satır `{"type":"end","job_id","last_seq","count","state"}`. `--after SEQ` o
    sıradan sonrasını verir. `--follow` Ctrl+C ile durduğunda `end` satırı yazılmaz ve çıkış kodu 0'dır.
    `--json` ile tek belge yazılır (`--follow` ile birlikte kullanım hatasıdır).

Ağır içe aktarmalar (iş yöneticisi, Store) işlevlerin içindedir.
"""
from __future__ import annotations

import argparse
import dataclasses
from enum import Enum
from typing import Any, Dict, List, Optional

from sofascore_scraper.cli.commands import CommandResult, Invocation, command, group
from sofascore_scraper.cli.commands.sync import data_dir_of_settings, job_event_line
from sofascore_scraper.cli.output import JSON, STREAM_END, Translator
from sofascore_scraper.errors import NotFoundError, UsageError

KINDS = ("sync", "fetch", "refresh", "export", "backup", "restore", "clear", "migrate")
STATES = ("queued", "running", "succeeded", "partial", "failed", "cancelled", "interrupted")


def _count(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError(f"expected a number (1 or more), got {value!r}")
    return number


def _sequence(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = -1
    if number < 0:
        raise argparse.ArgumentTypeError(f"expected a sequence number (0 or more), got {value!r}")
    return number


def _list_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--limit", type=_count, default=20, metavar="N", help=t("ssc_help_jobs_limit"))
    parser.add_argument("--kind", dest="kinds", action="append", choices=KINDS, help=t("ssc_help_jobs_kind"))
    parser.add_argument("--state", dest="states", action="append", choices=STATES, help=t("ssc_help_jobs_state"))


def _id_argument(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("job_id", metavar="ID", help=t("ssc_help_jobs_id"))


def _tail_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    _id_argument(parser, t)
    parser.add_argument("--follow", action="store_true", help=t("ssc_help_jobs_follow"))
    parser.add_argument("--after", type=_sequence, default=0, metavar="SEQ", help=t("ssc_help_jobs_after"))


def _manager(create: bool = False) -> Optional[Any]:
    """Veri dizininin iş yöneticisi; dizin henüz bir depo değilse None (hiçbir şey oluşturulmaz)."""
    from sofascore_scraper.jobs.manager import JobManager
    from sofascore_scraper.store import JobStore, StoreError, open_store

    try:
        store = open_store(data_dir_of_settings(), create=create)
    except StoreError as e:
        if type(e) is StoreError:
            return None
        raise
    return JobManager(JobStore.for_store(store))


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def job_dict(job: Any) -> Dict[str, Any]:
    """Bir iş, JSON için (HTTP API'nin `Job` modeliyle aynı alanlar)."""
    data: Dict[str, Any] = _plain(dataclasses.asdict(job))
    return data


def job_line(t: Translator, job: Any) -> str:
    when = job.finished_at or job.started_at or job.created_at or "?"
    error = f" ({job.error.code})" if job.error is not None else ""
    return t("ssc_jobs_line", id=job.id, kind=_plain(job.kind), state=_plain(job.state) + error, when=when,
             face=job.origin.face)


def _get(manager: Optional[Any], job_id: str) -> Any:
    job = manager.get(job_id) if manager is not None else None
    if job is None:
        raise NotFoundError(f"no job with id {job_id!r}", {"job_id": job_id})
    return job


group("jobs", help="ssc_help_cmd_jobs")


@command("jobs list", help="ssc_help_cmd_jobs_list", configure=_list_arguments, settings=True)
def jobs_list(inv: Invocation) -> CommandResult:
    args = inv.args
    manager = _manager()
    found: List[Any] = []
    if manager is not None:
        found = manager.list(limit=args.limit, kinds=args.kinds or None, states=args.states or None)
    text = "\n".join(job_line(inv.t, job) for job in found) if found else inv.t("ssc_jobs_none")
    return CommandResult(data={"jobs": [job_dict(job) for job in found]}, text=text)


@command("jobs show", help="ssc_help_cmd_jobs_show", configure=_id_argument, settings=True)
def jobs_show(inv: Invocation) -> CommandResult:
    job = _get(_manager(), inv.args.job_id)
    data = job_dict(job)
    lines = [job_line(inv.t, job)]
    if job.error is not None:
        lines.append(inv.t("ssc_jobs_error", code=job.error.code, message=job.error.message))
    if job.result:
        lines.append(inv.t("ssc_jobs_result", result=", ".join(
            f"{key}={value}" for key, value in sorted(job.result.items()) if isinstance(value, (int, float, str)))))
    return CommandResult(data={"job": data}, text="\n".join(lines))


@command("jobs cancel", help="ssc_help_cmd_jobs_cancel", configure=_id_argument, settings=True)
def jobs_cancel(inv: Invocation) -> CommandResult:
    manager = _manager()
    job = _get(manager, inv.args.job_id)
    assert manager is not None
    cancelled = bool(manager.cancel(job.id))
    data = {"job_id": job.id, "cancelled": cancelled, "state": _plain(job.state)}
    if not cancelled:
        return CommandResult(data=data, notes=[inv.t("ssc_jobs_not_running", id=job.id, state=_plain(job.state))])
    return CommandResult(data=data, text=inv.t("ssc_jobs_cancel_requested", id=job.id))


@command("jobs tail", help="ssc_help_cmd_jobs_tail", configure=_tail_arguments, settings=True)
def jobs_tail(inv: Invocation) -> CommandResult:
    args = inv.args
    as_document = inv.out.mode == JSON
    if args.follow and as_document:
        raise UsageError("--follow writes a stream of lines; use it without --json (or with --output ndjson)")
    manager = _manager()
    job = _get(manager, args.job_id)
    assert manager is not None
    collected: List[Dict[str, Any]] = []
    position, count = int(args.after), 0
    if not as_document:
        inv.out.begin_stream()
    try:
        for event in manager.events(job.id, after=args.after, follow=bool(args.follow)):
            line = job_event_line(event)
            position = event.seq
            count += 1
            if as_document:
                collected.append(line)
            else:
                inv.out.line(line)
    except KeyboardInterrupt:
        if not args.follow:
            raise
        return CommandResult()  # izleme durduruldu: `end` satırı yok, çıkış kodu 0
    final = manager.get(job.id) or job
    state = _plain(final.state)
    if as_document:
        return CommandResult(data={"job_id": job.id, "last_seq": position, "count": count, "state": state,
                                   "events": collected})
    inv.out.line({"type": STREAM_END, "job_id": job.id, "last_seq": position, "count": count, "state": state})
    return CommandResult()


__all__ = ["KINDS", "STATES", "job_dict", "job_line"]
