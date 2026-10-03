"""
`export`: saklanan maçları açık bir biçimde yazar (docs/design/02-services.md bölüm 4.1; plan maddesi P19).
Bugün iki biçim vardır; normalleştirilmiş şemanın veri kümeleri (`--schema normalized`) SC-2 ile gelir.

    ssc export                                        geniş CSV (`legacy-wide-csv`), veri klasörüne:
                                                      match_details/processed/all_matches_<zaman>.csv
    ssc export --out maclar.csv                       aynı CSV, verilen dosyaya
    ssc export --out - > maclar.csv                   aynı CSV, stdout'a
    ssc export --tournament 17 --out pl.csv           yalnızca bir turnuvanın maçları
    ssc export --schema raw --format jsonl --out ham.jsonl   saklanan ham yükler, dilim başına bir satır
    ssc export --schema raw --format tree --out ham/         ham yükler, maç başına bir dizin

  * Yalnızca detayı (olay yükü) saklanan maçlar yazılır. Yazılacak maç yoksa hiçbir dosya yazılmaz ve komut
    `not_found` ile biter (çıkış kodu 1).
  * Dışa aktarma kilit almaz: bir indirme sürerken de çalışır, o anki katalogdan okur.
  * Ham dışa aktarmanın hedefi zaten varsa `--force` olmadan depolama hatasıdır (çıkış kodu 5).

Ağır içe aktarmalar (servisler, Store) işlevlerin içindedir.
"""
from __future__ import annotations

import argparse
import dataclasses
from typing import Any, Dict, Optional

from src.cli.commands import CommandResult, Invocation, command
from src.cli.commands.sync import data_dir_of_settings
from src.cli.output import JSON, Translator
from src.errors import NotFoundError, NotSupportedError, UsageError

PROFILES = ("legacy-wide-csv",)
SCHEMAS = ("normalized", "raw")
FORMATS = ("csv", "jsonl", "tree")
STDOUT = "-"
LEGACY_EXPORT_DIR = ("match_details", "processed")


def _positive_id(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive id, got {value!r}")
    return number


def _arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--profile", choices=PROFILES, help=t("ssc_help_export_profile"))
    parser.add_argument("--schema", choices=SCHEMAS, help=t("ssc_help_export_schema"))
    parser.add_argument("--format", dest="fmt", choices=FORMATS, help=t("ssc_help_export_format"))
    parser.add_argument("--out", metavar="PATH|-", help=t("ssc_help_export_out"))
    parser.add_argument("--tournament", dest="tournaments", action="append", type=_positive_id, metavar="ID",
                        help=t("ssc_help_export_tournament"))
    parser.add_argument("--event", dest="events", action="append", type=_positive_id, metavar="ID",
                        help=t("ssc_help_export_event"))
    parser.add_argument("--force", action="store_true", help=t("ssc_help_export_force"))


def _raw(inv: Invocation, store: Any) -> CommandResult:
    from src.store import EventQuery, Scope

    args = inv.args
    fmt = args.fmt or "jsonl"
    if fmt not in ("jsonl", "tree"):
        raise UsageError("--schema raw writes --format jsonl or --format tree")
    if not args.out or args.out == STDOUT:
        raise UsageError("--schema raw needs --out PATH (a file for jsonl, a directory for tree)")
    query = EventQuery(scope=Scope(tournament_ids=tuple(args.tournaments or ()), event_ids=tuple(args.events or ())),
                       has_details=True)
    if store.events.count(query) == 0:
        raise NotFoundError("there is no downloaded match to export", {"filters": _filters(args)})
    report = store.export.raw(query, inv.resolve_path(args.out), fmt=fmt, overwrite=bool(args.force))
    data = {"schema": "raw", "format": fmt, "path": report.dest, "events": report.events, "items": report.items,
            "bytes": report.bytes, "skipped": [dataclasses.asdict(skip) for skip in report.skipped]}
    return CommandResult(data=data, text=inv.t("ssc_export_raw_written", path=report.dest, events=report.events,
                                                items=report.items))


def _filters(args: argparse.Namespace) -> Dict[str, Any]:
    return {"tournaments": list(args.tournaments or ()), "events": list(args.events or ())}


@command("export", help="ssc_help_cmd_export", configure=_arguments, settings=True)
def export(inv: Invocation) -> CommandResult:
    import os

    from src.services.export import LEGACY_WIDE_CSV, ExportService, ExportSpec
    from src.store import open_store

    args = inv.args
    if args.out == STDOUT and inv.out.mode == JSON:
        raise UsageError("--out - writes the export to stdout; use it without --json")
    if args.schema == "normalized":
        raise NotSupportedError("the normalized datasets are not available yet; use --profile legacy-wide-csv "
                                "or --schema raw", {"schema": "normalized"})
    data_dir = data_dir_of_settings()
    store = open_store(data_dir)
    if args.schema == "raw":
        if args.profile:
            raise UsageError("--profile is a CSV profile; it cannot be used with --schema raw")
        return _raw(inv, store)
    if args.fmt not in (None, "csv"):
        raise UsageError("the legacy-wide-csv profile writes --format csv")

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
                from src.exceptions import StorageError

                raise StorageError.from_exception(e, target) from e
        else:
            from src.utils import ensure_directory

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


__all__ = ["FORMATS", "PROFILES", "SCHEMAS"]
