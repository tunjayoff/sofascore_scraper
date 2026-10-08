"""
2.x'in `python main.py` bayrakları: 3.0.0'da kullanımdan kalktılar, 3.1'de silindiler (plan maddesi P30).

Kaldırılan bir bayrakla çalıştırma hiçbir şey yapmaz: kullanım hatasıdır (çıkış kodu 2) ve hata iletisi bayrağın
yerine geçen komutu söyler (`--headless --update-all` → `ssc sync`). Denetim yalnızca komuttan önceki
argümanlara bakar: `ssc sync --recheck-unavailable` gibi bir komutun kendi seçenekleri etkilenmez.

    --headless --update-all [--fetch-mode details] [--league-id N]   sync [--only events] [--tournament N]
    --headless --csv-export                                          export
    --refresh-only [--league-id N] [--refresh-legacy]                refresh [--tournament N] [--include-legacy]
    --recheck-unavailable[=legacy|all] [--league-id N]               data recheck-unavailable [--all] [--tournament N]
    --watch --sport S --league-ids A,B --event-ids X --watch-hours H watch --source poll --stdout --sport S
                                                                     --tournament A --event X --hours H
    --doctor [seçenekler]                                            doctor [seçenekler]
    --diagnostics [YOL]                                              diagnostics [--out YOL]
    --web [--host H] [--port P] [--dev] [--allow-any-host]           serve [--host H] [--port P] ...
    --ignore-rate-limit                                              --ignore-breaker

Bu modül yalnızca standart kütüphaneyi içe aktarır.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

# Eylem bayrakları, eski önceliğiyle (ilk eşleşen komutu belirler) → yerine geçen komut
ACTIONS: Tuple[Tuple[str, str], ...] = (
    ("--diagnostics", "diagnostics"),
    ("--doctor", "doctor"),
    ("--web", "serve"),
    ("--watch", "watch --source poll --stdout"),
    ("--refresh-only", "refresh"),
    ("--update-all", "sync"),
    ("--csv-export", "export"),
    ("--recheck-unavailable", "data recheck-unavailable"),
)
# Seçenek bayrakları → yeni yazılışları
OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("--headless", ""),
    ("--fetch-mode", "--only events"),
    ("--league-id", "--tournament"),
    ("--refresh-legacy", "--include-legacy"),
    ("--league-ids", "--tournament"),
    ("--event-ids", "--event"),
    ("--watch-hours", "--hours"),
    ("--ignore-rate-limit", "--ignore-breaker"),
)
REMOVED_FLAGS = frozenset(name for name, _new in (*ACTIONS, *OPTIONS))


def _flag_name(arg: str) -> str:
    return arg.split("=", 1)[0]


def removed_in(argv: Sequence[str], commands: Iterable[str]) -> List[str]:
    """Komuttan önce verilmiş kaldırılan bayraklar, verildikleri sırayla (yoksa boş)."""
    names = frozenset(commands)
    found: List[str] = []
    for arg in argv:
        if arg == "--" or arg in names:
            break
        name = _flag_name(arg)
        if name in REMOVED_FLAGS and name not in found:
            found.append(name)
    return found


def replacement(found: Sequence[str]) -> Optional[str]:
    """Kaldırılan bayrakların yerine geçen komut (`sync --tournament`); eylem bayrağı yoksa yalnızca seçenekler."""
    command = next((new for flag, new in ACTIONS if flag in found), None)
    options = [new for flag, new in OPTIONS if flag in found and new]
    parts = ([command] if command else []) + options
    return " ".join(parts) if parts else None


def message(found: Sequence[str], prog: str) -> str:
    """Kullanım hatasının iletisi (İngilizce, CLI'nin öteki hata iletileri gibi)."""
    flags = " ".join(found)
    instead = replacement(found)
    text = f"{flags}: the flags of `python main.py` were removed in 3.1 (deprecated in 3.0.0)"
    if instead:
        text += f"; use `{prog} {instead}`"
    return text + f" (`{prog} --help` lists the commands)"


__all__ = ["ACTIONS", "OPTIONS", "REMOVED_FLAGS", "message", "removed_in", "replacement"]
