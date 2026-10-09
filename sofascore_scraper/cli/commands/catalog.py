"""
`catalog rebuild|verify|reconcile`: veri dizininin kataloğunu (`.meta/catalog.db`) yönetir (docs/design/01-storage.md
bölüm 3.4-3.6, docs/design/02-services.md bölüm 4.1). Katalog, yük dosyalarının türetilmiş dizinidir: silinebilir
ve dosyalardan yeniden kurulur. Bu komutlar `scripts/catalog_tool.py`'nin yerini alır.

    ssc catalog verify                  kataloğu dosyalarla karşılaştırır (imzalarla, hızlı)
    ssc catalog verify --deep           her maçı ve v3 yüklerinin hepsini yeniden okur
    ssc catalog verify --repair         tutmayanları dosyalardan yeniden dizinler
    ssc catalog reconcile               arkasından değişen dosyaları yeniden dizinler (açılıştaki sınıra bakmaz)
    ssc catalog reconcile --deep        her şeyi yeniden okur, sonra onararak doğrular
    ssc catalog rebuild [--mode M]      kataloğu dosyalardan baştan kurar

  * Depo açılırken katalog kendiliğinden güncellenmez: komut kataloğu bulunduğu haliyle görür ve işi kendisi
    yapar. `reconcile` her zaman bütün maç dizinlerine bakar (açılıştaki bir dakikalık sınır, karar S17, burada
    uygulanmaz).
  * `rebuild`, `verify --repair` ve `reconcile --deep` `maintenance` kilidini alır: aynı veri dizininde bir
    indirme, canlı servis ya da izleyici çalışıyorsa reddedilir (çıkış kodu 6). Hızlı `verify` ve
    `reconcile` kilit almaz.
  * Çıkış kodu: tutarlı ya da kurulum tamamlandıysa 0; giderilmemiş tutarsızlık ya da tamamlanmamış kurulum 1.
  * Sorunların kodları (`no_event_directory`, `corrupt` ...) ve ayrıntıları İngilizcedir (Store'un metni; JSON
    çıktısında da öyle). Metin çıktısı kodun yanına uygulama dilinde kısa bir açıklama ekler
    (`ssc_catalog_kind_<kod>`, `ssc_catalog_problem_<kod>`); bilinmeyen kodun açıklaması yazılmaz.

Ağır içe aktarmalar (ayarlar, Store) işlevin içindedir (sofascore_scraper/cli/commands/__init__.py).
"""
from __future__ import annotations

import argparse
import dataclasses
import os
from typing import Any, Dict, List, Sequence

from sofascore_scraper.cli import exit_codes
from sofascore_scraper.cli.commands import CommandResult, Invocation, command, group
from sofascore_scraper.cli.commands.migrate import listed
from sofascore_scraper.cli.output import Translator

REBUILD_MODES = ("auto", "in_place", "recreate")

group("catalog", help="ssc_help_cmd_catalog")


def _rebuild_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--mode", choices=REBUILD_MODES, default="auto", help=t("ssc_help_catalog_mode"))


def _verify_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--deep", action="store_true", help=t("ssc_help_catalog_verify_deep"))
    parser.add_argument("--repair", action="store_true", help=t("ssc_help_catalog_repair"))


def _reconcile_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--deep", action="store_true", help=t("ssc_help_catalog_reconcile_deep"))


def _service(inv: Invocation) -> Any:
    """
    Veri dizininin bakım servisi. Depo, katalog bulunduğu haliyle açılır (açılışta kurulmaz, uzlaştırılmaz).
    Komut depoyu kapatmaz: süreç biter (aynı süreçte çağıran kodun açık deposu da aynı nesnedir).
    """
    from sofascore_scraper.config import loader
    from sofascore_scraper.services.maintenance import MaintenanceService
    from sofascore_scraper.store import open_store

    data_dir = os.path.abspath(loader.active_settings().storage.data_dir)
    return MaintenanceService(store=open_store(data_dir, sync_catalog=False))


def _label(t: Translator, prefix: str, kind: str) -> str:
    """Kodun uygulama dilindeki kısa açıklaması, " (…)" biçiminde; çevirisi olmayan kod için boş."""
    key = f"{prefix}{kind}"
    text = t(key)
    return f" ({text})" if text and text != key else ""


def _problem_lines(problems: Sequence[Any], t: Translator) -> List[str]:
    return [f"[{p.layout}] {p.kind}{_label(t, 'ssc_catalog_problem_', p.kind)}: {p.path}"
            + (f" ({p.detail})" if p.detail else "") for p in problems]


def _superseded_lines(t: Translator, superseded: Sequence[Any]) -> List[str]:
    return [t("ssc_catalog_superseded_line", event=s.event_id, path=s.path, winner=s.winner) for s in superseded]


@command("catalog rebuild", help="ssc_help_cmd_catalog_rebuild", configure=_rebuild_arguments, settings=True)
def catalog_rebuild(inv: Invocation) -> CommandResult:
    t = inv.t
    report = _service(inv).rebuild_catalog(mode=inv.args.mode)
    lines = [t("ssc_catalog_rebuilt", mode=report.mode, events=report.events, v3=report.events_v3,
               legacy=report.events_legacy, slices=report.slices, seconds=f"{report.seconds:.2f}")]
    if not report.completed:
        lines.append(t("ssc_catalog_rebuild_incomplete"))
    lines += listed(t, t("ssc_catalog_problems"), _problem_lines(report.problems, t))
    lines += listed(t, t("ssc_catalog_superseded"), _superseded_lines(t, report.superseded))
    code = exit_codes.OK if report.completed else exit_codes.GENERAL_ERROR
    return CommandResult(data=dataclasses.asdict(report), text="\n".join(lines), exit_code=code)


@command("catalog verify", help="ssc_help_cmd_catalog_verify", configure=_verify_arguments, settings=True)
def catalog_verify(inv: Invocation) -> CommandResult:
    t = inv.t
    report = _service(inv).verify_catalog(deep=inv.args.deep, repair=inv.args.repair)
    repaired = t("ssc_catalog_repaired")
    issues = [
        f"{issue.invariant} {issue.kind}{_label(t, 'ssc_catalog_kind_', issue.kind)}"
        + (" " + t("ssc_catalog_event", event=issue.event_id) if issue.event_id is not None else "")
        + (f" {issue.path}" if issue.path else "")
        + (f": {issue.detail}" if issue.detail else "")
        + (f" [{repaired}]" if issue.repaired else "")
        for issue in report.issues
    ]
    lines = [t("ssc_catalog_checked", rules=", ".join(report.checked), events=report.events,
               read=report.events_read, seconds=f"{report.seconds:.2f}")]
    lines += listed(t, t("ssc_catalog_issues"), issues)
    lines += listed(t, t("ssc_catalog_leftovers"), report.leftovers)
    lines += listed(t, t("ssc_catalog_problems"), _problem_lines(report.problems, t))
    lines += listed(t, t("ssc_catalog_superseded"), _superseded_lines(t, report.superseded))
    lines.append(t("ssc_catalog_consistent") if report.ok
                 else t("ssc_catalog_inconsistent", count=len(report.open_issues)))
    data: Dict[str, Any] = {**dataclasses.asdict(report), "ok": report.ok}
    return CommandResult(data=data, text="\n".join(lines),
                         exit_code=exit_codes.OK if report.ok else exit_codes.GENERAL_ERROR)


@command("catalog reconcile", help="ssc_help_cmd_catalog_reconcile", configure=_reconcile_arguments, settings=True)
def catalog_reconcile(inv: Invocation) -> CommandResult:
    t = inv.t
    report = _service(inv).reconcile_catalog(deep=inv.args.deep)
    ok = report.verify is None or report.verify.ok
    lines = [t("ssc_catalog_reconciled", checked=report.events_checked, indexed=report.events_indexed,
               removed=report.events_removed, pending=report.pending, seasons=len(report.seasons),
               seconds=f"{report.seconds:.2f}")]
    lines += listed(t, t("ssc_catalog_problems"), _problem_lines(report.problems, t))
    if report.verify is not None:
        lines.append(t("ssc_catalog_consistent") if ok
                     else t("ssc_catalog_inconsistent", count=len(report.verify.open_issues)))
    data: Dict[str, Any] = {**dataclasses.asdict(report), "changed": report.changed, "ok": ok}
    return CommandResult(data=data, text="\n".join(lines),
                         exit_code=exit_codes.OK if ok else exit_codes.GENERAL_ERROR)


__all__ = ["REBUILD_MODES"]
