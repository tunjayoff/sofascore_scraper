"""
`export`: saklanan veriyi açık bir biçimde yazar (docs/design/02-services.md bölüm 4.1; plan maddeleri P19 ve SC-2).
Üç tür dışa aktarma vardır:

    ssc export --dataset events --out maclar.jsonl           normalleştirilmiş şemanın kayıtları (şema v1),
    ssc export --dataset events --format csv --out m.csv     satır başına bir kayıt (JSONL) ya da düzleştirilmiş
    ssc export --dataset slices --format sqlite --out d.db   tablo (CSV, Parquet, SQLite); veri kümeleri events,
    ssc export --dataset changes --format parquet --out c.parquet   slices ve changes
    ssc export --schema normalized                           events, JSONL, veri klasörüne: exports/events_<zaman>.jsonl

    ssc export --schema raw --format jsonl --out ham.jsonl   saklanan ham yükler, yük başına bir satır
    ssc export --schema raw --format tree --out ham/         ham yükler, maç başına bir dizin

    ssc export                                               geniş CSV (`legacy-wide-csv`), veri klasörüne:
                                                             match_details/processed/all_matches_<zaman>.csv
    ssc export --out maclar.csv                              aynı CSV, verilen dosyaya
    ssc export --out - > maclar.csv                          aynı CSV, stdout'a

  * Hangisi: `--schema raw` ham dışa aktarmadır; `--dataset` ya da `--schema normalized` normalleştirilmiş veri
    kümesidir; ikisi de yoksa (bugünkü gibi) geniş CSV'dir.
  * Süzgeçler (`--sport`, `--tournament`, `--season`, `--event`, `--status`, `--from`, `--to`) birlikte (VE)
    uygulanır ve API v1'in `GET /events` süzgeçleriyle aynı anlamdadır. Geniş CSV yalnızca `--tournament` ve
    `--event`'i bilir; `changes` sezon ve durum süzgeci almaz, `--from`/`--to` onda kaydın zamanıdır.
  * Yazılacak kayıt yoksa hiçbir dosya yazılmaz ve komut `not_found` ile biter (çıkış kodu 1). Ham dışa aktarma
    ve geniş CSV yalnızca detayı (olay yükü) saklanan maçları yazar.
  * Dışa aktarma kilit almaz: bir indirme sürerken de çalışır, o anki katalogdan okur.
  * Veri kümesinin ya da ham dışa aktarmanın hedefi zaten varsa `--force` olmadan depolama hatasıdır (çıkış
    kodu 5). Parquet için `pyarrow` gerekir; yoksa `not_supported`.

Ağır içe aktarmalar (servisler, Store) işlevlerin içindedir.
"""
from __future__ import annotations

import argparse
import dataclasses
from typing import TYPE_CHECKING, Any, Dict, Optional, TextIO

from sofascore_scraper.cli.commands import CommandResult, Invocation, command
from sofascore_scraper.cli.commands.sync import data_dir_of_settings
from sofascore_scraper.cli.output import JSON, Translator
from sofascore_scraper.errors import NotFoundError, UsageError

if TYPE_CHECKING:
    from sofascore_scraper.services.export import DatasetSpec

PROFILES = ("legacy-wide-csv",)
SCHEMAS = ("normalized", "raw")
DATASETS = ("events", "slices", "changes", "odds", "standings")
FORMATS = ("csv", "jsonl", "tree", "parquet", "sqlite")
STATUS_CLASSES = ("not_started", "live", "completed", "decided_without_play", "void", "unknown")
STDOUT = "-"
LEGACY_EXPORT_DIR = ("match_details", "processed")
DATASET_EXPORT_DIR = "exports"  # sofascore_scraper/store/layout.py EXPORTS_DIR
RAW_DEFAULT_DATASET = "slices"  # ham dışa aktarma bugünkü gibi her yükü yazar


def _positive_id(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive id, got {value!r}")
    return number


def _arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--dataset", choices=DATASETS, help=t("ssc_help_export_dataset"))
    parser.add_argument("--profile", choices=PROFILES, help=t("ssc_help_export_profile"))
    parser.add_argument("--schema", choices=SCHEMAS, help=t("ssc_help_export_schema"))
    parser.add_argument("--format", dest="fmt", choices=FORMATS, help=t("ssc_help_export_format"))
    parser.add_argument("--out", metavar="PATH|-", help=t("ssc_help_export_out"))
    parser.add_argument("--sport", metavar="SLUG", help=t("ssc_help_export_sport"))
    parser.add_argument("--tournament", dest="tournaments", action="append", type=_positive_id, metavar="ID",
                        help=t("ssc_help_export_tournament"))
    parser.add_argument("--season", dest="seasons", action="append", type=_positive_id, metavar="ID",
                        help=t("ssc_help_export_season"))
    parser.add_argument("--event", dest="events", action="append", type=_positive_id, metavar="ID",
                        help=t("ssc_help_export_event"))
    parser.add_argument("--status", dest="statuses", action="append", choices=STATUS_CLASSES,
                        help=t("ssc_help_export_status"))
    parser.add_argument("--from", dest="start_from", metavar="DATE", help=t("ssc_help_export_from"))
    parser.add_argument("--to", dest="start_to", metavar="DATE", help=t("ssc_help_export_to"))
    parser.add_argument("--force", action="store_true", help=t("ssc_help_export_force"))


def _filters(args: argparse.Namespace) -> Dict[str, Any]:
    return {"tournaments": list(args.tournaments or ()), "events": list(args.events or ())}


def _dataset_spec(args: argparse.Namespace, dataset: str, fmt: str, schema: str) -> "DatasetSpec":
    """Argümanlar → servisin belirtimi; okunamayan `--from`/`--to` `invalid_request`."""
    from sofascore_scraper.services.export import DatasetFilter, DatasetSpec, parse_moment

    moments: Dict[str, Optional[float]] = {}
    for name, text, end in (("from", args.start_from, False), ("to", args.start_to, True)):
        try:
            moments[name] = None if text is None else parse_moment(text, end=end)
        except ValueError:
            raise UsageError(f"--{name} expects an ISO 8601 date or date-time", {name: text}) from None
    return DatasetSpec(
        dataset=dataset, format=fmt, schema=schema,
        filter=DatasetFilter(sport=args.sport, tournament_ids=tuple(args.tournaments or ()),
                             season_ids=tuple(args.seasons or ()), event_ids=tuple(args.events or ()),
                             status_classes=tuple(args.statuses or ()), start_from=moments["from"],
                             start_to=moments["to"]))


class _TextSink:
    """Satır yazıcısının baytlarını bir metin akışına (stdout) verir; yazıcı her çağrıda bütün satırlar yazar."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(self, data: bytes) -> int:
        self._stream.write(data.decode("utf-8"))
        return len(data)


def _raw(inv: Invocation, store: Any) -> CommandResult:
    from sofascore_scraper.services.export import ExportService, check_dataset

    args = inv.args
    fmt = args.fmt or "jsonl"
    if fmt not in ("jsonl", "tree"):
        raise UsageError("--schema raw writes --format jsonl or --format tree")
    if not args.out or args.out == STDOUT:
        raise UsageError("--schema raw needs --out PATH (a file for jsonl, a directory for tree)")
    dataset = args.dataset or RAW_DEFAULT_DATASET
    spec = _dataset_spec(args, dataset, fmt, "raw")
    check_dataset(spec)
    result = ExportService(store).export_dataset(spec, inv.resolve_path(args.out), overwrite=bool(args.force),
                                                 allow_empty=False)
    data = {"schema": "raw", "dataset": dataset, "format": fmt, "path": result.path, "events": result.events,
            "items": result.rows, "bytes": result.bytes,
            "skipped": [dataclasses.asdict(skip) for skip in result.skipped]}
    return CommandResult(data=data, text=inv.t("ssc_export_raw_written", path=result.path, events=result.events,
                                                items=result.rows))


def _normalized(inv: Invocation, data_dir: str) -> CommandResult:
    import os
    import time

    from sofascore_scraper.services.export import TEXT_FORMATS, ExportService, check_dataset
    from sofascore_scraper.store import open_store

    args = inv.args
    if args.profile:
        raise UsageError("--profile is the 2.x wide CSV; it cannot be used with --dataset or --schema normalized")
    fmt = args.fmt or "jsonl"
    dataset = args.dataset or "events"
    if args.out == STDOUT and fmt not in TEXT_FORMATS:
        raise UsageError(f"--format {fmt} is a binary file; write it with --out PATH")
    spec = _dataset_spec(args, dataset, fmt, "normalized")
    check_dataset(spec)  # geçersiz birleşim ya da pyarrow yok: veri klasörü açılmadan
    service = ExportService(open_store(data_dir))

    if args.out == STDOUT:
        inv.out.begin_stream()  # stdout dışa aktarmanındır: sonuç zarfı yazılmaz
        stream = inv.out.raw_stream()
        result = service.export_dataset(spec, _TextSink(stream), allow_empty=False)  # type: ignore[arg-type]
        stream.flush()
    else:
        target = (inv.resolve_path(args.out) if args.out
                  else os.path.join(data_dir, DATASET_EXPORT_DIR, f"{dataset}_{int(time.time())}.{fmt}"))
        result = service.export_dataset(spec, target, overwrite=bool(args.force), allow_empty=False)
    data = {"dataset": dataset, "schema": "normalized", "schema_version": result.schema_version, "format": fmt,
            "path": result.path, "rows": result.rows, "columns": len(result.columns), "bytes": result.bytes}
    if result.path is None:
        return CommandResult(data=data, notes=[inv.t("ssc_export_dataset_streamed", rows=result.rows,
                                                     dataset=dataset)])
    return CommandResult(data=data, text=inv.t("ssc_export_dataset_written", path=result.path, rows=result.rows,
                                                dataset=dataset))


@command("export", help="ssc_help_cmd_export", configure=_arguments, settings=True)
def export(inv: Invocation) -> CommandResult:
    import os

    from sofascore_scraper.services.export import LEGACY_WIDE_CSV, ExportService, ExportSpec
    from sofascore_scraper.store import open_store

    args = inv.args
    if args.out == STDOUT and inv.out.mode == JSON:
        raise UsageError("--out - writes the export to stdout; use it without --json")
    data_dir = data_dir_of_settings()
    if args.schema == "raw":
        if args.profile:
            raise UsageError("--profile is a CSV profile; it cannot be used with --schema raw")
        return _raw(inv, open_store(data_dir))
    if args.schema == "normalized" or args.dataset is not None:
        return _normalized(inv, data_dir)
    if args.fmt not in (None, "csv"):
        raise UsageError("the legacy-wide-csv profile writes --format csv; use --dataset for the other formats")
    if args.sport or args.seasons or args.statuses or args.start_from or args.start_to:
        raise UsageError("the legacy-wide-csv profile filters by --tournament and --event only")
    store = open_store(data_dir)

    spec = ExportSpec(profile=LEGACY_WIDE_CSV, tournament_ids=tuple(args.tournaments or ()),
                      event_ids=tuple(args.events or ()))
    service = ExportService(store)
    prepared = service.prepare(spec)
    if prepared.rows == 0:
        # Boş bir dosya yazılmaz: "dışa aktarılacak maç yok" bir hatadır (eski `--csv-export` 0 ile çıkıyordu)
        raise NotFoundError("there is no downloaded match to export", {"filters": _filters(args)})

    target: Optional[str]
    if args.out == STDOUT:
        inv.out.begin_stream()  # stdout dışa aktarmanındır: sonuç zarfı yazılmaz
        stream = inv.out.raw_stream()
        result = service.export(spec, stream)
        stream.flush()
        target = None
    else:
        if args.out:
            target = inv.resolve_path(args.out)
            try:
                result = service.export(spec, target)
            except OSError as e:  # dizin, izin yok, dolu disk: depolama hatası (çıkış kodu 5)
                from sofascore_scraper.exceptions import StorageError

                raise StorageError.from_exception(e, target) from e
        else:
            from sofascore_scraper.utils import ensure_directory

            directory = os.path.join(data_dir, *LEGACY_EXPORT_DIR)
            ensure_directory(directory)
            written = service.write_legacy_csv(directory, spec)
            if written is None:  # bu arada silindi
                raise NotFoundError("there is no downloaded match to export", {"filters": _filters(args)})
            result = written
        target = result.path
    data = {"profile": LEGACY_WIDE_CSV, "format": "csv", "path": target, "rows": result.rows,
            "columns": len(result.columns), "bytes": result.bytes}
    text = inv.t("ssc_export_written", path=target, rows=result.rows)
    if target is None:
        return CommandResult(data=data, notes=[inv.t("ssc_export_streamed", rows=result.rows)])
    return CommandResult(data=data, text=text)


__all__ = ["DATASETS", "FORMATS", "PROFILES", "SCHEMAS", "STATUS_CLASSES"]
