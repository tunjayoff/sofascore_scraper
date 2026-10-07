"""
`backup`: veri klasörünün yedekleri (docs/design/01-storage.md bölüm 9; plan maddesi ST-24).

    ssc backup create                       her şey: state.db, v3 ağacı, eski ağaçlar, değişiklik günlüğü, ayarlar
    ssc backup create --scope state         yalnızca durum (takipler, iş geçmişi, olay günlüğü) ve ayarlar
    ssc backup create --include-secrets     `.env` de pakete girer (proxy parolası, belirteçler)
    ssc backup list                         `backups/` altındaki yedekler, en yeni önce
    ssc backup verify <ad>                  yedeği okur ve denetler (çıkış kodu 1: sorun var)
    ssc backup restore <ad> --dry-run       ne olacağını söyler, hiçbir şey yazmaz
    ssc backup restore <ad> --yes           boş bir veri klasörüne geri yükler
    ssc backup restore <ad> --force --yes   klasördeki veriyi önce çöpe taşır, sonra geri yükler

  * Yedekler `<veri klasörü>/backups/` altındadır; geri yükleme yalnızca oradaki bir yedeği adıyla alır.
    Bugünkü (biçim 1, 2.x) zip'ler de geri yüklenir: veri ağaçları eski düzen ağaçları olarak yerine konur.
  * `create` veri içeren kapsamlarda `writer` kilidini (amaç `op:backup`) alır: bir indirme sürerken
    `job_running` ya da başka bir veri işlemi sürerken `data_operation_running` ile çıkar (çıkış kodu 6).
    `restore` `maintenance` kilidini alır: çalışan bir iş, canlı servis ya da sunucu varken çıkar.
  * `restore` `--yes` olmadan `confirmation_required` ile çıkar (deneme çalıştırması hariç). Veri klasörü boş
    değilse (v3 ya da eski düzen ağaçları, API'den eklenmiş takipler) `--force` gerekir; birleştirme yoktur.
  * Ayar dosyaları ve `.env` geri yüklenmez (veri klasörünün dışındadırlar); sonuç onları `skipped` olarak verir.

Ağır içe aktarmalar (ayarlar, Store) işlevlerin içindedir (sofascore_scraper/cli/commands/__init__.py).
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List

from sofascore_scraper.cli.commands import CliWarning, CommandResult, Invocation, command, group
from sofascore_scraper.cli.output import Translator
from sofascore_scraper.errors import UsageError

SCOPES = ("all", "state", "data", "config", "seasons", "matches", "match_details")
LOCKED_SCOPES = tuple(scope for scope in SCOPES if scope != "config")  # `writer` kilidi altında yazılır

group("backup", help="ssc_help_cmd_backup")


def _store() -> Any:
    from sofascore_scraper.config import loader
    from sofascore_scraper.store import open_store

    return open_store(os.path.abspath(loader.active_settings().storage.data_dir))


def _size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"  # pragma: no cover - döngü her zaman döner


def _info(info: Any) -> Dict[str, Any]:
    return {"name": info.name, "path": info.path, "scope": info.scope, "with_env": info.with_env,
            "created_at": info.created_at, "bytes": info.size, "format": info.format}


def _config_files() -> List[str]:
    """Pakete giren ayar dosyaları: lig listesi, spor eşlemesi ve kullanılan yapılandırma dosyası (varsa)."""
    from sofascore_scraper.config import loader
    from sofascore_scraper.paths import config_dir, default_league_config_path

    found = [default_league_config_path(), os.path.join(config_dir(), "league_sports.json")]
    config_file = loader.active().config_file
    if config_file:
        found.append(config_file)
    return found


# --- create ---------------------------------------------------------------------------------------------

def _create_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--scope", choices=SCOPES, default="all", help=t("ssc_help_backup_scope"))
    parser.add_argument("--include-secrets", action="store_true", help=t("ssc_help_backup_include_secrets"))


@command("backup create", help="ssc_help_cmd_backup_create", configure=_create_arguments, settings=True)
def backup_create(inv: Invocation) -> CommandResult:
    from sofascore_scraper.services.backup import BackupService

    store = _store()
    scope = inv.args.scope
    service = BackupService(store)
    if scope in LOCKED_SCOPES:
        with store.lease("writer", purpose="op:backup"):
            info = service.create(scope, config_files=_config_files(), include_secrets=inv.args.include_secrets)
    else:
        info = service.create(scope, config_files=_config_files(), include_secrets=inv.args.include_secrets)
    warnings: List[CliWarning] = []
    if info.with_env:
        warnings.append(CliWarning("backup_with_secrets",
                                   "the backup contains the .env file (proxy password, tokens); keep it private"))
    text = inv.t("ssc_backup_created", path=info.path, size=_size(info.size))
    return CommandResult(data=_info(info), text=text, warnings=warnings)


# --- list -----------------------------------------------------------------------------------------------

@command("backup list", help="ssc_help_cmd_backup_list", settings=True)
def backup_list(inv: Invocation) -> CommandResult:
    from sofascore_scraper.services.backup import BackupService

    store = _store()
    found = BackupService(store).list()
    data = {"directory": store.backup.directory, "backups": [_info(info) for info in found]}
    if not found:
        return CommandResult(data=data, text=inv.t("ssc_backup_none", path=store.backup.directory))
    lines = []
    for info in found:
        fmt = inv.t("ssc_backup_format", format=info.format if info.format is not None else "?")
        env = "  .env" if info.with_env else ""
        lines.append(f"{info.name}  {info.scope}  {info.created_at}  {_size(info.size)}  {fmt}{env}")
    return CommandResult(data=data, text="\n".join(lines))


# --- verify ---------------------------------------------------------------------------------------------

def _name_argument(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("name", help=t("ssc_help_backup_name"))


@command("backup verify", help="ssc_help_cmd_backup_verify", configure=_name_argument, settings=True)
def backup_verify(inv: Invocation) -> CommandResult:
    from sofascore_scraper.services.backup import BackupService

    check = BackupService(_store()).verify(inv.args.name)
    counts = check.manifest.get("counts")
    data = {"name": check.name, "ok": check.ok, "format": check.format, "scope": check.scope,
            "members": check.members, "bytes": check.bytes, "problems": list(check.problems),
            "counts": dict(counts) if isinstance(counts, dict) else {}}
    if check.ok:
        text = inv.t("ssc_backup_ok", name=check.name, format=check.format, members=check.members)
        return CommandResult(data=data, text=text)
    text = "\n".join([inv.t("ssc_backup_problems", name=check.name, count=len(check.problems)),
                      *(f"  {problem}" for problem in check.problems)])
    return CommandResult(data=data, text=text, exit_code=1)


# --- restore --------------------------------------------------------------------------------------------

def _restore_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    _name_argument(parser, t)
    parser.add_argument("--force", action="store_true", help=t("ssc_help_backup_force"))
    parser.add_argument("--dry-run", action="store_true", help=t("ssc_help_backup_dry_run"))
    parser.add_argument("--yes", action="store_true", help=t("ssc_help_backup_yes"))


@command("backup restore", help="ssc_help_cmd_backup_restore", configure=_restore_arguments, settings=True)
def backup_restore(inv: Invocation) -> CommandResult:
    from sofascore_scraper.services.backup import BackupService

    args = inv.args
    if not args.dry_run and not args.yes:
        raise UsageError("a restore replaces the state and the data of the data folder; add --yes "
                         "(or check first with --dry-run)", code="confirmation_required")
    store = _store()
    report = BackupService(store).restore(args.name, force=args.force, dry_run=args.dry_run)
    data = {
        "name": report.name, "format": report.format, "scope": report.scope, "dry_run": report.dry_run,
        "force": report.force, "restored": list(report.restored), "replaced": list(report.replaced),
        "skipped": list(report.skipped), "occupied": list(report.occupied), "counts": dict(report.counts),
        "catalog_rebuilt": report.catalog_rebuilt, "verify_ok": report.verify_ok,
        "verify_issues": report.verify_issues,
    }
    restored = ", ".join(report.restored) or "-"
    notes: List[str] = []
    if report.skipped:
        notes.append(inv.t("ssc_backup_skipped", members=", ".join(report.skipped)))
    if report.dry_run:
        lines = [inv.t("ssc_backup_restore_plan", name=report.name, entries=restored)]
        if report.occupied:
            lines.append(inv.t("ssc_backup_restore_occupied", entries=", ".join(report.occupied)))
        return CommandResult(data=data, text="\n".join(lines), notes=notes)
    text = inv.t("ssc_backup_restored", name=report.name, path=str(store.data_dir), entries=restored)
    if report.verify_ok is False:
        warning = CliWarning("restore_check_failed",
                             f"the check after the restore found {report.verify_issues} issue(s)")
        return CommandResult(data=data, text=text, notes=notes, warnings=[warning], exit_code=1)
    return CommandResult(data=data, text=text, notes=notes)
