"""
`migrate`: eski (2.x) düzende saklanan veriyi yeni düzene (v3) taşır (docs/design/01-storage.md bölüm 5.4,
docs/design/02-services.md bölüm 4.1). Açık bir komuttur: uygulama eski düzeni kendiliğinden taşımaz.

    ssc migrate --dry-run                     ne yapılacağını gösterir, hiçbir şeyi değiştirmez
    ssc migrate --dry-run --exact             boyut tahmini bütün maçlar sıkıştırılarak (yavaş)
    ssc migrate                               dönüştürür ve doğrular; eski kopya yerinde kalır
    ssc migrate --tournament 17 --limit 100   yalnızca bir turnuva, en çok 100 maç (tekrar çalıştırınca sürer)
    ssc migrate --delete-legacy --yes         doğrulanan eski kopyaları da siler (sonradan da çalıştırılabilir)
    ssc migrate --purge-derived --yes         türetilmiş dosyaları (sezon özetleri, processed/) siler

  * `--delete-legacy` ve `--purge-derived` yıkıcıdır: `--yes` olmadan `confirmation_required` (çıkış kodu 2).
    Kuru çalıştırmada `--yes` gerekmez.
  * Bir indirme, canlı servis ya da izleyici aynı veri dizininde çalışırken reddedilir (çıkış kodu 6).
  * Bazı girdiler dönüştürülemezse ya da eski kopyaları doğrulanamazsa iş biter ve çıkış kodu 3'tür (kısmi
    başarı); onların eski kopyası yerinde kalır. Sonuç `--json` ile tam listesiyle yazılır.
  * Komut önce yapılandırmadaki ligleri takip tablosuna yansıtır (uygulamanın her başlangıçta yaptığı gibi):
    adında turnuva kimliği olmayan sezon listesi dosyaları (`<ad>_seasons.json`) ancak böyle çözülür.

Ağır içe aktarmalar (ayarlar, Store) işlevin içindedir (src/cli/commands/__init__.py).
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.cli import exit_codes
from src.cli.commands import CommandResult, Invocation, command
from src.cli.output import TEXT, Translator
from src.errors import UsageError

logger = logging.getLogger(__name__)

LISTED = 20  # metin çıktısında bir listeden en çok bu kadar satır yazılır (--json hepsini verir)
PROGRESS_SECONDS = 2.0  # metin kipinde ilerleme satırları en çok bu sıklıkla yazılır (stderr)


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError(f"expected a positive number, got {value!r}")
    return number


def _tournament(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = -1
    if number < 0:
        raise argparse.ArgumentTypeError(f"expected a tournament id, got {value!r}")
    return number


def _arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help=t("ssc_help_migrate_dry_run"))
    parser.add_argument("--exact", action="store_true", help=t("ssc_help_migrate_exact"))
    parser.add_argument("--tournament", dest="tournaments", action="append", type=_tournament, metavar="ID",
                        help=t("ssc_help_migrate_tournament"))
    parser.add_argument("--limit", type=_positive, metavar="N", help=t("ssc_help_migrate_limit"))
    parser.add_argument("--delete-legacy", dest="delete_legacy", action="store_true",
                        help=t("ssc_help_migrate_delete_legacy"))
    parser.add_argument("--purge-derived", dest="purge_derived", action="store_true",
                        help=t("ssc_help_migrate_purge_derived"))
    parser.add_argument("--yes", action="store_true", help=t("ssc_help_migrate_yes"))


def human_bytes(count: int) -> str:
    """Bayt sayısı okunur biçimde (dil bağımsız birimler): 512 B, 6.6 MB."""
    value = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{count} B"  # erişilmez


def listed(t: Translator, title: str, lines: Sequence[str]) -> List[str]:
    """Başlıklı bir liste: en çok LISTED satır, kalanın sayısı."""
    if not lines:
        return []
    out = [f"{title} ({len(lines)}):"]
    out.extend(f"  {line}" for line in lines[:LISTED])
    if len(lines) > LISTED:
        out.append("  " + t("ssc_list_more", count=len(lines) - LISTED))
    return out


def _issue_lines(issues: Sequence[Any]) -> List[str]:
    return [f"{issue.key}: {issue.path}" + (f" ({issue.detail})" if issue.detail else "") for issue in issues]


def _progress(inv: Invocation) -> Optional[Callable[[Any], None]]:
    """Metin kipinde ve `--quiet` olmadan stderr'e seyrek ilerleme satırları."""
    if inv.out.mode != TEXT or inv.out.quiet:
        return None
    last = [0.0]

    def report(progress: Any) -> None:
        now = time.monotonic()
        if progress.done < progress.total and now - last[0] < PROGRESS_SECONDS:
            return
        last[0] = now
        inv.out.info(inv.t("ssc_migrate_progress", stage=progress.stage, done=progress.done, total=progress.total))

    return report


def _mirror_follows(data_dir: str) -> None:
    """Yapılandırmadaki ligler takip tablosuna (uygulamanın başlangıcındaki ayna); başarısızlık taşımayı durdurmaz."""
    try:
        from src.config_manager import ConfigManager

        ConfigManager().mirror_follows(data_dir)
    except Exception as exc:  # ayna ikincil bir kayıttır (ConfigManager._mirror_follows ile aynı kural)
        logger.warning("Leagues could not be mirrored into the follows table before the migration: %s", exc)


# Değişiklik günlüğü kopyasının durumu (src/store/changes.py `COPY_*`) → metin anahtarı
CHANGE_LOG_TEXTS: Dict[str, str] = {
    "none": "ssc_migrate_log_none",
    "created": "ssc_migrate_log_created",
    "extended": "ssc_migrate_log_extended",
    "unchanged": "ssc_migrate_log_unchanged",
    "conflict": "ssc_migrate_log_conflict",
}


def _change_log(t: Translator, state: str) -> str:
    return t(CHANGE_LOG_TEXTS.get(state, "ssc_migrate_log_none"))


def _deleted_log(t: Translator, report: Any) -> str:
    if report.change_log_deleted:
        return t("ssc_migrate_log_deleted")
    return _change_log(t, "none") if report.change_log == "none" else t("ssc_migrate_log_kept")


def _plan_text(t: Translator, plan: Any) -> str:
    lines = [t("ssc_migrate_plan", events=plan.events, pages=plan.schedule_pages, seasons=plan.schedule_seasons,
               lists=plan.season_lists, change_log=_change_log(t, plan.change_log))]
    if plan.delete_legacy:
        lines.append(t("ssc_migrate_plan_delete", copies=plan.legacy_copies))
    how = (t("ssc_migrate_size_exact") if plan.exact
           else t("ssc_migrate_size_sampled", count=plan.sampled))
    lines.append(t("ssc_migrate_plan_size", before=human_bytes(plan.bytes_before),
                   after=human_bytes(plan.bytes_after), how=how))
    if plan.stopped:
        lines.append(t("ssc_migrate_plan_limited", limit=plan.limit))
    lines += listed(t, t("ssc_migrate_extra"), plan.extra_files)
    lines += listed(t, t("ssc_migrate_conflicts"), _issue_lines(plan.conflicts))
    lines += listed(t, t("ssc_migrate_unconvertible"), _issue_lines(plan.unconvertible))
    lines += listed(t, t("ssc_migrate_derived"), plan.derived_files)
    return "\n".join(lines)


def _report_text(t: Translator, report: Any) -> str:
    lines = [t("ssc_migrate_done", events=report.events, existing=report.events_existing,
               pages=report.schedule_pages, lists=report.season_lists,
               change_log=_change_log(t, report.change_log))]
    if report.delete_legacy:
        lines.append(t("ssc_migrate_deleted", events=report.legacy_copies, pages=report.schedule_files_deleted,
                       lists=report.season_list_files_deleted,
                       change_log=_deleted_log(t, report)))
    lines.append(t("ssc_migrate_size", before=human_bytes(report.bytes_before), after=human_bytes(report.bytes_after),
                   seconds=f"{report.seconds:.1f}"))
    if report.stopped:
        lines.append(t("ssc_migrate_stopped"))
    lines += listed(t, t("ssc_migrate_failed"), _issue_lines(report.failed))
    lines += listed(t, t("ssc_migrate_conflicts"), _issue_lines(report.conflicts))
    lines += listed(t, t("ssc_migrate_kept"), _issue_lines(report.legacy_kept))
    lines += listed(t, t("ssc_migrate_unconvertible"), _issue_lines(report.unconvertible))
    lines += listed(t, t("ssc_migrate_extra"), report.extra_files)
    lines += listed(t, t("ssc_migrate_derived_removed"), report.derived_removed)
    return "\n".join(lines)


@command("migrate", help="ssc_help_cmd_migrate", configure=_arguments, settings=True)
def migrate(inv: Invocation) -> CommandResult:
    args = inv.args
    if args.exact and not args.dry_run:
        raise UsageError("--exact is an option of --dry-run")
    if (args.delete_legacy or args.purge_derived) and not args.dry_run and not args.yes:
        flags = " and ".join(flag for flag, given in (("--delete-legacy", args.delete_legacy),
                                                     ("--purge-derived", args.purge_derived)) if given)
        raise UsageError(f"{flags} deletes files: add --yes to confirm (or try --dry-run first)",
                         code="confirmation_required")

    from src.config import loader
    from src.services.maintenance import MaintenanceService
    from src.store import open_store

    data_dir = os.path.abspath(loader.active_settings().storage.data_dir)
    store = open_store(data_dir)
    _mirror_follows(data_dir)
    service = MaintenanceService(store=store)
    if args.dry_run:
        service.reconcile_catalog()  # takiplerden gelen lig adlarıyla güncel iş listesi; yalnızca katalog yazılır
    result = service.migrate(
        dry_run=args.dry_run, exact=args.exact, tournaments=args.tournaments or (), limit=args.limit,
        delete_legacy=args.delete_legacy, purge_derived=args.purge_derived, confirm=args.yes,
        progress=_progress(inv),
    )
    data: Dict[str, Any] = dataclasses.asdict(result)
    data["dry_run"] = bool(args.dry_run)
    if args.dry_run:
        return CommandResult(data=data, text=_plan_text(inv.t, result))
    data["ok"] = result.ok
    code = exit_codes.OK if result.ok else exit_codes.PARTIAL
    return CommandResult(data=data, text=_report_text(inv.t, result), exit_code=code)


__all__ = ["CHANGE_LOG_TEXTS", "human_bytes", "listed"]
