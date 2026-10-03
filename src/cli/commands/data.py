"""
Veri bakımı (docs/design/02-services.md bölüm 4.1; plan maddesi P19): `data clear` ve `data recheck-unavailable`.
İkisi de SofaScore'a istek atmaz.

    ssc data clear --scope events --yes          maç detaylarını siler (eski ve v3 kopyaları birlikte)
    ssc data clear --all --yes                   detayları, programları ve sezon listelerini siler
    ssc data recheck-unavailable                 eski sürümlerden kalan "bu dilim yok" işaretlerini açar
    ssc data recheck-unavailable --all --tournament 17

  * `clear` yıkıcıdır: `--yes` olmadan `confirmation_required` (çıkış kodu 2). Silinen her şey, eski düzendeki
    (`match_details/`, `matches/`, `seasons/`) ve yeni düzendeki (`v3/`) kopyalarıyla birlikte gider (karar
    S18); katalog kalan dosyalardan yeniden kurulur. Takipler, ayarlar, iş geçmişi ve olay günlüğü kalır.
    `maintenance` kilidini alır: bir indirme, canlı servis ya da başka bir bakım işi çalışırken reddedilir
    (çıkış kodu 6).
  * `recheck-unavailable` dilimleri bir sonraki indirmede yeniden istenecek hale getirir; `writer` kilidini
    tutar (bir indirmeyle aynı anda çalışmaz; `--wait` kilidi bekler).

Ağır içe aktarmalar (servisler, Store) işlevlerin içindedir.
"""
from __future__ import annotations

import argparse
import dataclasses
from typing import Any, Dict

from src.cli.commands import CommandResult, Invocation, command, group
from src.cli.commands.sync import build_service_context, data_dir_of_settings, option, results_only_on_stdout
from src.cli.output import Translator
from src.errors import UsageError

# Store'un kapsam adları (docs/design/01-storage.md 9.3); `all` üçüdür
CLEAR_SCOPES = ("events", "schedules", "seasons", "all")
RECHECK_PURPOSE = "recheck-unavailable"


def _positive_id(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive id, got {value!r}")
    return number


def _clear_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--scope", choices=CLEAR_SCOPES, help=t("ssc_help_data_clear_scope"))
    parser.add_argument("--all", dest="everything", action="store_true", help=t("ssc_help_data_clear_all"))
    parser.add_argument("--yes", action="store_true", help=t("ssc_help_data_yes"))


def _recheck_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--all", dest="everything", action="store_true", help=t("ssc_help_data_recheck_all"))
    parser.add_argument("--tournament", type=_positive_id, metavar="ID", help=t("ssc_help_data_recheck_tournament"))


group("data", help="ssc_help_cmd_data")


@command("data clear", help="ssc_help_cmd_data_clear", configure=_clear_arguments, settings=True)
def data_clear(inv: Invocation) -> CommandResult:
    args = inv.args
    if args.everything and args.scope not in (None, "all"):
        raise UsageError("give either --scope or --all")
    scope = "all" if args.everything else args.scope
    if scope is None:
        raise UsageError("say what to clear: --scope events|schedules|seasons|all, or --all")
    if not args.yes:
        raise UsageError(f"clearing {scope} deletes data: add --yes to confirm", code="confirmation_required")

    from src.services.maintenance import MaintenanceService
    from src.store import open_store

    store = open_store(data_dir_of_settings())
    report = MaintenanceService(store=store).clear(scope, confirm=True)  # type: ignore[arg-type]
    data: Dict[str, Any] = {"scope": scope, **dataclasses.asdict(report)}
    cleared = ", ".join(report.cleared + (("v3/events",) if report.v3_events else ())) or "-"
    return CommandResult(data=data, text=inv.t("ssc_data_cleared", scope=scope, cleared=cleared))


@command("data recheck-unavailable", help="ssc_help_cmd_data_recheck", configure=_recheck_arguments, settings=True)
def data_recheck_unavailable(inv: Invocation) -> CommandResult:
    from src.services.maintenance import MaintenanceService

    args = inv.args
    with results_only_on_stdout():
        ctx = build_service_context(data_dir_of_settings())
        # Bir indirmenin yazdığı işaretlerle çakışmasın: yazar kilidi (iş değildir, iş geçmişine yazılmaz)
        with ctx.store.lease("writer", purpose=RECHECK_PURPOSE, wait=float(option(inv, "wait", 0.0))):
            reset = MaintenanceService(ctx).recheck_unavailable(args.tournament, include_confirmed=args.everything)
    data = {"tournament": args.tournament, "include_confirmed": bool(args.everything), **dataclasses.asdict(reset)}
    text = inv.t("recheck_unavailable_done", matches=reset.matches, slices=reset.slices, scanned=reset.scanned)
    return CommandResult(data=data, text=text)


__all__ = ["CLEAR_SCOPES"]
