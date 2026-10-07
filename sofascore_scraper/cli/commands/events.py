"""
`events`: olay günlüğünü okur (docs/design/02-services.md bölüm 4.1 ve 5). Çalışan bir servis gerekmez; kilit
almaz ve günlüğe yazmaz.

    ssc events                              günlükteki bütün olaylar, baştan
    ssc events --stream job --limit 20      yalnızca `job` akışı, en çok 20 olay
    ssc events --after 1842                 1842'den sonraki olaylar (kalınan yerden)
    ssc events --follow --type 'live.*'     yeni canlı olayları yazıldıkça izle (Ctrl+C ile durur)

Çıktı (bölüm 4.4): akış komutudur, stdout'a satır başına bir nesne yazar (NDJSON). Her satır bir olay zarfıdır
(`sofascore.event/1`; `type` olayın türüdür); akış kendiliğinden bittiğinde son satır şudur:

    {"type":"end","stream_id":"...","last_seq":1860,"count":18,"gap":false}

  * `last_seq`  kalınan yer: bir sonraki çağrıya `--after` olarak verilir. Sıra numaraları artar ama
                ardışık değildir.
  * `stream_id` günlüğün kimliği: değiştiyse (state.db yeniden yaratılmış) saklanan sıra numarası anlamsızdır.
  * `gap`       `--after`'dan sonraki olayların bir kısmı okunmadan budanmış (günlük 7 gün tutar).

`--json` ile tek bir belge yazılır (zarfın `data` alanı: `stream_id`, `last_seq`, `count`, `gap`, `events`);
`--follow` bir belgeye sığmaz, `--json` ile birlikte kullanım hatasıdır. `--follow` Ctrl+C ya da SIGTERM ile
durduğunda `end` satırı yazılmaz ve çıkış kodu 0'dır. Akışın ortasındaki bir hata stdout'a
`{"type":"error","error":{...},"exit_code":N}` satırı olarak yazılır; okuyan boruyu erken kapatırsa (`ssc events |
head -1`) çıkış kodu 0'dır (sofascore_scraper/cli/output.py, plan maddesi P19).

Henüz depo olmayan bir veri dizininde (hiçbir komut çalışmamış) olay yoktur: boş sonuç döner; `--follow`
izleyecek bir günlük bulamaz ve depolama hatasıyla biter.

Ağır içe aktarmalar (ayarlar, Store, sink'ler) işlevin içindedir (sofascore_scraper/cli/commands/__init__.py).
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List, Optional

from sofascore_scraper.cli.commands import CliWarning, CommandResult, Invocation, command
from sofascore_scraper.cli.output import JSON, STREAM_END, Translator
from sofascore_scraper.errors import UsageError

# Günlüğün akışları (sofascore_scraper/store/streams.py STREAMS ile aynı; tests/test_sinks.py eşitliği sınar)
STREAMS = ("live", "change", "job", "system")
PAGE = 500  # günlükten bir okumada alınan satır
FOLLOW_WAIT_SECONDS = 1.0  # `--follow`: yeni olay bu aralıklarla beklenir (bekleme yeni olayda hemen biter)
END_TYPE = STREAM_END


def _sequence(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = -1
    if number < 0:
        raise argparse.ArgumentTypeError(f"expected a sequence number (0 or more), got {value!r}")
    return number


def _count(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError(f"expected a number of events (1 or more), got {value!r}")
    return number


def _arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--stream", action="append", choices=STREAMS, help=t("ssc_help_events_stream"))
    parser.add_argument("--after", type=_sequence, metavar="SEQ", help=t("ssc_help_events_after"))
    parser.add_argument("--follow", action="store_true", help=t("ssc_help_events_follow"))
    parser.add_argument("--type", dest="types", action="append", metavar="TYPE", help=t("ssc_help_events_type"))
    parser.add_argument("--event", dest="event_ids", action="append", type=int, metavar="ID",
                        help=t("ssc_help_events_event"))
    parser.add_argument("--limit", type=_count, metavar="N", help=t("ssc_help_events_limit"))


def _open(data_dir: str) -> Any:
    """Veri dizininin deposu; dizin henüz bir depo değilse None (hiçbir şey oluşturulmaz)."""
    from sofascore_scraper.store import StoreError, open_store

    try:
        return open_store(data_dir, readonly=True, create=False)
    except StoreError as e:
        # "Henüz depo değil" hatası veri dizininin kendisini taşır; diğer hatalar (bozuk dosya, daha yeni şema) çıkar
        if type(e) is StoreError and e.path and os.path.normcase(str(e.path)) == os.path.normcase(data_dir):
            return None
        raise


def _end(stream_id: Optional[str], last_seq: int, count: int, gap: bool) -> Dict[str, Any]:
    return {"type": END_TYPE, "stream_id": stream_id, "last_seq": last_seq, "count": count, "gap": gap}


def _gap_warning(after: int) -> CliWarning:
    return CliWarning(
        "stream_gap",
        f"events after sequence {after} were pruned from the event log before they were read; some are missing",
    )


@command("events", help="ssc_help_cmd_events", configure=_arguments, settings=True)
def events(inv: Invocation) -> CommandResult:
    args = inv.args
    follow = bool(args.follow)
    as_document = inv.out.mode == JSON
    if follow and as_document:
        raise UsageError("--follow writes a stream of lines; use it without --json (or with --output ndjson)")

    from sofascore_scraper.config import loader
    from sofascore_scraper.sinks import Envelope, EventFilter
    from sofascore_scraper.sinks.stdout import StdoutSink

    try:
        flt = EventFilter(args.types or ("*",), event_ids=args.event_ids or ())
    except ValueError:
        raise UsageError("--type: expected an event type or a pattern such as 'live.*'") from None
    data_dir = os.path.abspath(loader.active_settings().storage.data_dir)
    after: int = args.after if args.after is not None else 0

    store = _open(data_dir)
    if store is None:
        if follow:
            from sofascore_scraper.store import StoreError

            raise StoreError(f"the data directory has no event log yet: {data_dir}", path=data_dir)
        note = inv.t("ssc_events_no_store", path=data_dir)
        if as_document:
            return CommandResult(data={"stream_id": None, "last_seq": after, "count": 0, "gap": False, "events": []})
        inv.out.line(_end(None, after, 0, False))
        return CommandResult(notes=[note])

    streams = store.streams
    if follow and args.after is None:
        after = streams.head().last_seq  # `--follow` "şimdi"den başlar
    collected: List[Dict[str, Any]] = []
    if not as_document:
        inv.out.begin_stream()  # satırlar stdout'a: sonuç zarfı yazılmaz, hata bir akış satırı olur
    sink = StdoutSink("stdout", flt, write=inv.out.write)
    filters: Dict[str, Any] = {
        "streams": tuple(args.stream or ()),
        "types": flt.exact_types,  # kalıp varsa boş: tür süzgeci burada uygulanır
        "event_ids": tuple(args.event_ids or ()),
    }
    position, count, gap, stream_id = after, 0, False, None
    warnings: List[CliWarning] = []
    first_read = True
    try:
        while True:
            batch = streams.read(after=position, limit=PAGE, **filters)
            stream_id = batch.stream_id
            if first_read and batch.gap:
                gap = True
                warning = _gap_warning(after)
                if follow:  # izleme bitmeyebilir: uyarı hemen yazılır
                    inv.out.info(inv.t("ssc_warning", message=warning.message))
                else:
                    warnings.append(warning)
            first_read = False
            accepted = [env for env in (Envelope.from_record(record, stream_id) for record in batch.events)
                        if sink.accepts(env)]
            if args.limit is not None:
                accepted = accepted[:args.limit - count]
            if as_document:
                collected.extend(env.to_dict() for env in accepted)
            else:
                sink.deliver(accepted)
            count += len(accepted)
            if args.limit is not None and count >= args.limit:
                position = accepted[-1].seq  # sınırda durulan olay: `--after` buradan sürer
                break
            position = batch.last_seq
            if len(batch.events) == PAGE:
                continue  # günlükte daha var
            if not follow:
                break
            while not streams.wait(after=position, timeout=FOLLOW_WAIT_SECONDS):
                pass
    except KeyboardInterrupt:
        if not follow:
            raise
        return CommandResult(warnings=warnings)  # izleme durduruldu: `end` satırı yok, çıkış kodu 0

    if as_document:
        data = {"stream_id": stream_id, "last_seq": position, "count": count, "gap": gap, "events": collected}
        return CommandResult(data=data, warnings=warnings)
    inv.out.line(_end(stream_id, position, count, gap))
    return CommandResult(warnings=warnings)


__all__ = ["END_TYPE", "STREAMS"]
