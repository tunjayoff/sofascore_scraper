"""
`follows`: veri dizininin takipleri (state.db `follows` tablosu; docs/design/01-storage.md 2.3, 02-services.md
4.1; plan maddesi P19).

    ssc follows list                                   bütün takipler
    ssc follows add tournament 17 --name "Premier League" --sport football --live
    ssc follows remove tournament 17
    ssc follows export > follows.toml                  bütün takipler, yapılandırma dosyasının `[[follow]]` metni

  * Her takibin bir kaynağı vardır: `config` (yapılandırma dosyasının `[[follow]]` girdileri), `legacy`
    (`config/leagues.txt`) ya da `api` (bu komut ve HTTP API'si). Yapılandırma dosyasından gelen takip burada
    değiştirilemez ve silinemez (`follow_managed`); dosyada düzenlenir.
  * Canlı servis (`ssc watch`) `live = true` takipleri izler. İndirme (`ssc sync`) bugün ligleri yapılandırmadan
    okur (`config/leagues.txt`, `[[follow]]`): buradan eklenen bir turnuva takibi indirmeye henüz girmez (P27).
  * Takipler kilit almadan yazılır (tek satırlık state.db işlemi).

Ağır içe aktarmalar (ayarlar, Store) işlevlerin içindedir.
"""
from __future__ import annotations

import argparse
import dataclasses
from typing import Any, Dict, List, Optional

from src.cli.commands import CommandResult, Invocation, command, group
from src.cli.commands.sync import data_dir_of_settings
from src.cli.output import Translator
from src.errors import UsageError

KINDS = ("tournament", "team", "player", "event")


def _positive_id(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive id, got {value!r}")
    return number


def _list_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--kind", choices=KINDS, help=t("ssc_help_follows_kind"))


def _target_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("kind", choices=KINDS, metavar="KIND", help=t("ssc_help_follows_kind"))
    parser.add_argument("entity_id", type=_positive_id, metavar="ID", help=t("ssc_help_follows_id"))


def _add_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    _target_arguments(parser, t)
    parser.add_argument("--name", help=t("ssc_help_follows_name"))
    parser.add_argument("--sport", help=t("ssc_help_follows_sport"))
    parser.add_argument("--seasons", metavar="all|current|last:N|IDS", help=t("ssc_help_follows_seasons"))
    parser.add_argument("--live", action="store_true", help=t("ssc_help_follows_live"))
    parser.add_argument("--disabled", action="store_true", help=t("ssc_help_follows_disabled"))


def _open(create: bool) -> Optional[Any]:
    """Veri dizininin deposu; `create=False` ve dizin henüz depo değilse None (hiçbir şey oluşturulmaz)."""
    from src.store import StoreError, open_store

    try:
        return open_store(data_dir_of_settings(), create=create)
    except StoreError as e:
        if not create and type(e) is StoreError:
            return None
        raise


def follow_dict(follow: Any) -> Dict[str, Any]:
    data = dataclasses.asdict(follow)
    seasons = data.get("seasons")
    if isinstance(seasons, (list, tuple)):
        data["seasons"] = list(seasons)
    return data


def _line(t: Translator, follow: Any) -> str:
    flags = []
    if follow.live:
        flags.append("live")
    if not follow.enabled:
        flags.append("disabled")
    seasons = follow.seasons if isinstance(follow.seasons, str) else ",".join(str(s) for s in follow.seasons)
    return t("ssc_follows_line", kind=follow.kind, id=follow.entity_id, name=follow.name, sport=follow.sport or "-",
             seasons=seasons, origin=follow.origin, flags=(" [" + ", ".join(flags) + "]") if flags else "")


group("follows", help="ssc_help_cmd_follows")


@command("follows list", help="ssc_help_cmd_follows_list", configure=_list_arguments, settings=True)
def follows_list(inv: Invocation) -> CommandResult:
    store = _open(create=False)
    rows = store.follows.list(kind=inv.args.kind) if store is not None else []
    text = "\n".join(_line(inv.t, row) for row in rows) if rows else inv.t("ssc_follows_none")
    return CommandResult(data={"follows": [follow_dict(row) for row in rows]}, text=text)


@command("follows add", help="ssc_help_cmd_follows_add", configure=_add_arguments, settings=True)
def follows_add(inv: Invocation) -> CommandResult:
    from src.config import loader
    from src.store import FollowSpec

    args = inv.args
    table: Dict[str, Any] = {args.kind: args.entity_id, "live": bool(args.live), "enabled": not args.disabled}
    if args.name is not None:
        table["name"] = args.name
    if args.sport is not None:
        table["sport"] = args.sport
    if args.seasons is not None:
        table["seasons"] = _seasons(args.seasons)
    try:
        # Yapılandırma dosyasının `[[follow]]` kuralları: bilinen spor, sezon yazımı, ad
        (parsed,) = loader.parse_follows([table], loader.active_settings().defaults.seasons, "follows add")
    except Exception as e:
        raise UsageError(str(e).replace("follows add: [[follow]] #1", "follows add"), {"follow": table}) from None
    store = _open(create=True)
    assert store is not None
    added = store.follows.add(FollowSpec(
        kind=parsed.kind, entity_id=parsed.entity_id, name=parsed.name, sport=parsed.sport, seasons=parsed.seasons,
        slices=parsed.slices, live=parsed.live, enabled=parsed.enabled,
    ), origin="api")
    return CommandResult(data={"follow": follow_dict(added)}, text=inv.t("ssc_follows_added", line=_line(inv.t, added)))


def _seasons(raw: str) -> Any:
    """`--seasons`: "all", "current", "last:N" ya da virgülle ayrılmış sezon kimlikleri."""
    text = raw.strip()
    if text and all(part.strip().isdigit() for part in text.split(",")):
        return [int(part) for part in text.split(",")]
    return text


@command("follows remove", help="ssc_help_cmd_follows_remove", configure=_target_arguments, settings=True)
def follows_remove(inv: Invocation) -> CommandResult:
    args = inv.args
    store = _open(create=False)
    removed = bool(store is not None and store.follows.remove(args.kind, args.entity_id))
    data = {"kind": args.kind, "entity_id": args.entity_id, "removed": removed}
    if not removed:
        # Yinelenebilir: takip zaten yoksa sonuç aynıdır; çıkış kodu 0, bilgi stderr'e
        return CommandResult(data=data, notes=[inv.t("ssc_follows_not_found", kind=args.kind, id=args.entity_id)])
    return CommandResult(data=data, text=inv.t("ssc_follows_removed", kind=args.kind, id=args.entity_id))


def follows_text(rows: List[Any]) -> str:
    """Takipler, yapılandırma dosyasının `[[follow]]` tabloları olarak (src/config/loader.py parse_follows)."""
    from src.cli.commands.meta import toml_value

    lines: List[str] = []
    for row in rows:
        lines += ["[[follow]]", f"{row.kind} = {int(row.entity_id)}", f"name = {toml_value(row.name)}"]
        if row.sport:
            lines.append(f"sport = {toml_value(row.sport)}")
        seasons = row.seasons if isinstance(row.seasons, str) else list(row.seasons)
        lines.append(f"seasons = {toml_value(seasons)}")
        if row.slices:
            # Satır içi tablo: {include = [...]} ya da {enable = [...], disable = [...]}
            pairs = ", ".join(f"{key} = {toml_value(value)}" for key, value in dict(row.slices).items())
            lines.append(f"slices = {{ {pairs} }}")
        if row.live:
            lines.append("live = true")
        if not row.enabled:
            lines.append("enabled = false")
        lines.append("")
    return "\n".join(lines).rstrip("\n")


@command("follows export", help="ssc_help_cmd_follows_export", settings=True)
def follows_export(inv: Invocation) -> CommandResult:
    store = _open(create=False)
    rows = store.follows.list() if store is not None else []
    if not rows:
        return CommandResult(data={"follows": [], "toml": ""}, notes=[inv.t("ssc_follows_none")])
    text = follows_text(rows)
    return CommandResult(data={"follows": [follow_dict(row) for row in rows], "toml": text + "\n"}, text=text)


__all__ = ["KINDS", "follow_dict", "follows_text"]
