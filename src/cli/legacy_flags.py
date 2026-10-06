"""
`python main.py <eski bayraklar>`ın yeni CLI karşılığı (docs/design/02-services.md bölüm 4.7; plan maddesi P19).

`main.py` bir geçiş kabuğudur: bir alt komutla çağrılırsa (`python main.py sync`) doğrudan yeni CLI'ye gider; eski
bayraklar (`--headless --update-all`, `--refresh-only`, ...) bu modülde yeni komutlara çevrilir ve stderr'e tek
bir kullanımdan kalkma satırı yazılır. Takma adlar yeni çıkış kodlarını ve yeni çıktı kurallarını kullanır (karar
D5): loglar stderr'de, sonuç stdout'ta.

    --headless --update-all [--fetch-mode details] [--league-id N]   sync [--only events] [--tournament N]
    --headless --csv-export                                          export
    --refresh-only [--league-id N] [--refresh-legacy]                refresh [--tournament N] [--include-legacy]
    --recheck-unavailable[=legacy|all] [--league-id N]               data recheck-unavailable [--all] [--tournament N]
       ... --headless --update-all ile birlikte                      sync --recheck-unavailable [all]
    --watch --sport S --league-ids A,B --event-ids X --watch-hours H watch --source poll --stdout --sport S
                                                                     --tournament A --tournament B --event X --hours H
    --doctor [seçenekler]                                            doctor [seçenekler]
    --diagnostics [YOL]                                              diagnostics [--out YOL]
    --web [--host H] [--port P] [--dev] [--allow-any-host]           serve --host H --port P [--dev] [--allow-any-host]
    --ignore-rate-limit                                              --ignore-breaker
    --data-dir YOL                                                   --data-dir YOL
    --config YOL                                                     --config YOL (yapılandırma dosyası; eski lig
                                                                     dosyası `.txt` uyarıyla yok sayılır)

Bayraksız çalıştırma eskiden terminal menüsünü açardı; menü P26 ile kalktı ve `main.py` o durumda yeni komutları
anlatan yardımı yazar (`print_no_menu_help`). `--version` her zaman eskisi gibi yanıtlanır.

`--web` (P25) `serve`e çevrilir. Eski ayrıştırıcının varsayılanları (127.0.0.1, 8000) her zaman açıkça geçirilir:
`python main.py --web` bugün olduğu gibi yapılandırma dosyasının `[server] host` / `port` değerine bakmadan
127.0.0.1:8000'de açılır. Host izin listesi ve belirteç uyarısı kuralları `serve`inkidir (PR #43'ünkilerle aynı).

Eski `--watch` takma adı her zaman yoklama kaynağıyla çalışır (`--source poll`, karar D18): mevcut cron ve
systemd kurulumları, `page` varsayılan kaynak olduğu halde tarayıcı başlatmaz.

Bu modül yalnızca standart kütüphaneyi ve src/cli/output.py'yi içe aktarır.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from src.cli.output import Translator

# Çevirinin kullanım hataları; metinleri main.py'nin eski metinleridir (locales: cli_*)
NEEDS_ACTION = "headless_needs_action"
WATCH_USAGE = "watch_usage"

# Eski `--config` bir lig dosyası (config/leagues.txt) beklerdi; yeni anlamı yapılandırma dosyasıdır
LEGACY_LEAGUES_SUFFIX = ".txt"


@dataclass(frozen=True)
class Translation:
    """
    Eski bayrakların karşılığı.

    commands     sırayla çalıştırılacak yeni komutlar (her biri tam bir argv: genel bayraklar dahil)
    error        çevrilemeyen bir birleşim (NEEDS_ACTION, WATCH_USAGE): hiçbir komut çalışmaz, çıkış kodu 2
    interactive  eylem bayrağı yok: `main.py` yeni komutları anlatan yardımı yazar (eskiden terminal menüsü)
    warnings     İngilizce uyarılar (kullanımdan kalkma satırının yanında stderr'e yazılır)
    """

    commands: Tuple[Tuple[str, ...], ...] = ()
    error: Optional[str] = None
    interactive: bool = False
    warnings: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def runs_commands(self) -> bool:
        return bool(self.commands) and self.error is None


def _absolute(cwd: str, path: str) -> str:
    """Göreli yol kullanıcının çalıştırdığı dizine göre (yeni CLI her komutta proje köküne geçer)."""
    return os.path.abspath(os.path.join(cwd, os.path.expanduser(path)))


def _ids(raw: Optional[str]) -> List[str]:
    """Virgülle ayrılmış kimlikler; denetimi yeni komutun ayrıştırıcısı yapar (sayı değilse kullanım hatası)."""
    return [part.strip() for part in str(raw).split(",") if part.strip()] if raw else []


def global_flags(args: argparse.Namespace, cwd: str) -> Tuple[List[str], List[str]]:
    """Eski genel bayrakların yeni karşılıkları ve uyarılar."""
    flags: List[str] = []
    warnings: List[str] = []
    config = getattr(args, "config", None)
    if config:
        if str(config).lower().endswith(LEGACY_LEAGUES_SUFFIX):
            warnings.append(
                f"--config {config} looks like a leagues file; --config now names the configuration file "
                f"(sofascore.toml) and the leagues file is ignored, as it always was. Leagues are read from "
                f"config/leagues.txt or from [[follow]] in the configuration file."
            )
        else:
            flags += ["--config", _absolute(cwd, config)]
    if getattr(args, "data_dir", None):
        flags += ["--data-dir", _absolute(cwd, args.data_dir)]
    if getattr(args, "ignore_rate_limit", False):
        flags.append("--ignore-breaker")
    return flags, warnings


def _with(flags: Sequence[str], *command: str) -> Tuple[str, ...]:
    return (*flags, *command)


def _recheck(args: argparse.Namespace, flags: Sequence[str]) -> Tuple[str, ...]:
    command = ["data", "recheck-unavailable"]
    if args.recheck_unavailable == "all":
        command.append("--all")
    if args.league_id is not None:
        command += ["--tournament", str(args.league_id)]
    return _with(flags, *command)


def translate(args: argparse.Namespace, cwd: str) -> Translation:
    """
    `main.py`nin ayrıştırılmış eski bayrakları → yeni komutlar. Öncelik eskisi gibidir: --diagnostics, --web,
    --watch, sonra --refresh-only, --headless ve --recheck-unavailable; hiçbiri yoksa (eskiden terminal menüsü,
    P26 ile kalktı) bayraksız yardım.
    """
    flags, warnings = global_flags(args, cwd)
    notes = tuple(warnings)

    if args.diagnostics is not None:
        command = ["diagnostics"]
        if args.diagnostics:
            command += ["--out", _absolute(cwd, args.diagnostics)]
        return Translation(commands=(_with(flags, *command),), warnings=notes)

    if args.web:
        command = ["serve", "--host", str(args.host), "--port", str(args.port)]
        if args.allow_any_host:
            command.append("--allow-any-host")
        if args.dev:
            command.append("--dev")
        return Translation(commands=(_with(flags, *command),), warnings=notes)

    if args.watch:
        if not args.sport or not (args.league_ids or args.event_ids):
            return Translation(error=WATCH_USAGE, warnings=notes)
        command = ["watch", "--source", "poll", "--stdout", "--sport", args.sport]
        for tournament in _ids(args.league_ids):
            command += ["--tournament", tournament]
        for event in _ids(args.event_ids):
            command += ["--event", event]
        if args.watch_hours:
            command += ["--hours", repr(float(args.watch_hours))]
        return Translation(commands=(_with(flags, *command),), warnings=notes)

    league = ["--tournament", str(args.league_id)] if args.league_id is not None else []
    commands: List[Tuple[str, ...]] = []
    if args.refresh_only:
        # Bugünkü öncelik: --refresh-only verildiyse --headless'in eylemleri yapılmaz
        if args.recheck_unavailable:
            commands.append(_recheck(args, flags))
        command = ["refresh", *league]
        if args.refresh_legacy:
            command.append("--include-legacy")
        commands.append(_with(flags, *command))
        return Translation(commands=tuple(commands), warnings=notes)

    if args.headless:
        if not (args.update_all or args.csv_export):
            # Eskiden önce işaretler açılıyor, eksik eylem sonra fark ediliyordu: şimdi hiçbir şey yapılmaz
            return Translation(error=NEEDS_ACTION, warnings=notes)
        if args.update_all:
            command = ["sync", *league]
            if args.fetch_mode == "details":
                command += ["--only", "events"]
            if args.recheck_unavailable:
                command += ["--recheck-unavailable", args.recheck_unavailable]
            if args.refresh_legacy:
                command.append("--include-legacy")
            commands.append(_with(flags, *command))
        elif args.recheck_unavailable:
            commands.append(_recheck(args, flags))
        if args.csv_export:
            commands.append(_with(flags, "export"))
        return Translation(commands=tuple(commands), warnings=notes)

    if args.recheck_unavailable:
        return Translation(commands=(_recheck(args, flags),), warnings=notes)

    return Translation(interactive=True, warnings=notes)


def doctor_command(argv: Sequence[str]) -> Tuple[str, ...]:
    """`--doctor [seçenekler]` → `doctor [seçenekler]` (main.py'nin öteki bayrakları orada kullanım hatasıdır)."""
    return ("doctor", *[arg for arg in argv if arg != "--doctor"])


def command_line(prog: str, command: Sequence[str]) -> str:
    """Bir komutun kabukta yazılışı (kullanımdan kalkma satırı için); Windows'ta cmd.exe'nin tırnak kuralıyla."""
    if os.name == "nt":
        import subprocess

        return " ".join([prog, subprocess.list2cmdline(list(command))]).rstrip()
    import shlex

    return " ".join([prog, *(shlex.quote(part) for part in command)])


def deprecation_line(t: Translator, prog: str, commands: Sequence[Sequence[str]]) -> str:
    """Tek satır: eski bayraklar kullanımdan kalkıyor; bu çalıştırmanın yeni karşılığı."""
    return t("ssc_legacy_deprecated", commands=" && ".join(command_line(prog, command) for command in commands))


__all__ = [
    "NEEDS_ACTION",
    "WATCH_USAGE",
    "Translation",
    "command_line",
    "deprecation_line",
    "doctor_command",
    "global_flags",
    "translate",
]
