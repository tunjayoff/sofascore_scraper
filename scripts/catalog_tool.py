#!/usr/bin/env python3
"""
Kataloğu (DATA_DIR/.meta/catalog.db) elle yönetmek için küçük araç: yeniden kurma, uzlaştırma, doğrulama,
sayımlar.

Katalog, yük dosyalarının türetilmiş dizinidir (docs/design/01-storage.md, bölüm 3); silinebilir ve
dosyalardan yeniden kurulur. Uygulama kataloğu açılışta kurar ve yazdıkça günceller, ama henüz okumuyor
(gölge kip): bu araç onu denemek ve bir veri dizininde neyin dizinlendiğine bakmak içindir. Yerini
`catalog rebuild|reconcile|verify` CLI komutları alınca kaldırılacak.

Kullanım:
    python scripts/catalog_tool.py [--data-dir DİZİN] rebuild [--mode auto|in_place|recreate] [--json]
    python scripts/catalog_tool.py [--data-dir DİZİN] reconcile [--deep] [--v3] [--json]
    python scripts/catalog_tool.py [--data-dir DİZİN] verify [--deep] [--repair] [--json]
    python scripts/catalog_tool.py [--data-dir DİZİN] stats [--json]

Veri dizini: --data-dir, yoksa DATA_DIR ortam değişkeni, o da yoksa "data". `.env` dosyası okunmaz.

Dizin bir depoysa (`.meta/schema.json` ve `.meta/state.db` var) araç depoyu açar, ama açılıştaki otomatik
güncellemeyi çalıştırmaz: katalog bulunduğu haliyle görülür. O zaman
  * adında turnuva kimliği olmayan sezon listesi dosyaları (`<ad>_seasons.json`) turnuva takiplerindeki
    adlarla çözülür (uygulamanın açılışta yaptığı gibi);
  * `rebuild`, `reconcile --deep` ve `verify --repair`, `maintenance` kilidini alır: aynı dizinde bir indirme
    ya da izleyici çalışıyorsa reddedilir (çıkış kodu 3, kilidin sahibi yazılır).
Dizin henüz bir depo değilse (yalnızca dosyalar var) araç eskisi gibi yalnızca kataloğu açar; kilit yoktur ve
ad eşlemesi verilmez. O durumda yazan başka bir süreç olmadığından çağıran emin olmalıdır.

Çıkış kodları: 0 başarılı / tutarlı, 1 tutarsızlık bulundu ya da kurulum tamamlanmadı, 2 kullanım hatası
ya da veri dizini yok, 3 depolama hatası.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import sys
from typing import Any, Dict, Iterator, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.store import CatalogAdmin, RebuildReport, ReconcileReport, Store, StoreError, VerifyReport, open_store
from src.store import layout
from src.store.indexer import MODE_AUTO, MODE_IN_PLACE, MODE_RECREATE

EXIT_OK = 0
EXIT_ISSUES = 1
EXIT_USAGE = 2
EXIT_STORAGE = 3

_MAX_LISTED = 50  # metin çıktısında bir listeden en çok bu kadar satır yazılır (--json hepsini verir)


def _emit(data: Dict[str, Any]) -> None:
    print(json.dumps(data, indent=2))  # ASCII: uçbirimin kodlaması ne olursa olsun geçerli JSON


def _listed(title: str, lines: Sequence[str]) -> None:
    if not lines:
        return
    print(f"{title} ({len(lines)}):")
    for line in lines[:_MAX_LISTED]:
        print(f"  {line}")
    if len(lines) > _MAX_LISTED:
        print(f"  ... {len(lines) - _MAX_LISTED} satır daha (--json hepsini verir)")


def _problem_lines(report: Any) -> List[str]:
    return [f"[{p.layout}] {p.kind}: {p.path}" + (f" ({p.detail})" if p.detail else "") for p in report.problems]


def _superseded_lines(report: Any) -> List[str]:
    return [f"{s.event_id}: {s.path} (geçerli olan: {s.winner})" for s in report.superseded]


@contextlib.contextmanager
def _maintenance(store: Optional[Store], purpose: str) -> Iterator[None]:
    """Dizin bir depoysa blok boyunca `maintenance` kilidini tutar; değilse kilit yoktur (modül belgesi)."""
    if store is None:
        yield
        return
    with store.lease("maintenance", purpose=purpose):
        yield


def run_rebuild(admin: CatalogAdmin, args: argparse.Namespace, store: Optional[Store] = None) -> int:
    def progress(stage: str, done: int, total: int) -> None:
        if not args.json:
            print(f"  {stage}: {done}/{total}", file=sys.stderr)

    with _maintenance(store, "op:rebuild"):
        report: RebuildReport = admin.rebuild(mode=args.mode, progress=progress)
    if args.json:
        _emit(dataclasses.asdict(report))
    else:
        print(f"Katalog yeniden kuruldu ({report.mode}; önceki hali: {report.reason or 'kullanılabilir'})")
        print(f"  maç: {report.events} (v3 {report.events_v3}, eski düzen {report.events_legacy}), "
              f"dilim: {report.slices}, süre: {report.seconds:.2f} sn")
        _listed("Okunamayan ya da tanınmayan girdiler", _problem_lines(report))
        _listed("Kullanılmayan eski kopyalar", _superseded_lines(report))
    return EXIT_OK if report.completed else EXIT_ISSUES


def run_reconcile(admin: CatalogAdmin, args: argparse.Namespace, store: Optional[Store] = None) -> int:
    # Derin uzlaştırma onarım da yapar (verify --deep --repair): kilit ister. Hızlı olan tek bir yazma işlemidir.
    with _maintenance(store if args.deep else None, "op:reconcile"):
        report: ReconcileReport = admin.reconcile(deep=args.deep, v3=args.v3)
    ok = report.verify is None or report.verify.ok
    if args.json:
        _emit({**dataclasses.asdict(report), "changed": report.changed})
        return EXIT_OK if ok else EXIT_ISSUES
    print(f"Katalog uzlaştırıldı ({'derin' if report.deep else 'imzalarla'}): "
          f"{'değişiklik var' if report.changed else 'değişiklik yok'}")
    print(f"  bakılan maç: {report.events_checked}, yeniden dizinlenen: {report.events_indexed}, "
          f"silinen: {report.events_removed}, yarım yazma işareti: {report.pending}, süre: {report.seconds:.2f} sn")
    print(f"  sezon listeleri: {'değişmedi' if report.season_lists is None else report.season_lists}, "
          f"değişiklik günlüğü: {'değişmedi' if report.changes is None else report.changes}")
    _listed("Listesi yeniden dizinlenen sezonlar", [f"{tournament}/{season}" for tournament, season in report.seasons])
    _listed("Okunamayan ya da tanınmayan girdiler", _problem_lines(report))
    if report.verify is not None:
        print("Katalog dosyalarla tutarlı." if ok else
              f"{len(report.verify.open_issues)} tutarsızlık giderilmedi (rebuild).")
    return EXIT_OK if ok else EXIT_ISSUES


def run_verify(admin: CatalogAdmin, args: argparse.Namespace, store: Optional[Store] = None) -> int:
    with _maintenance(store if args.repair else None, "op:verify"):
        report: VerifyReport = admin.verify(deep=args.deep, repair=args.repair)
    if args.json:
        _emit({**dataclasses.asdict(report), "ok": report.ok})
    else:
        print(f"Denetlenen kurallar: {', '.join(report.checked)}")
        print(f"  maç: {report.events}, dosyaları yeniden okunan: {report.events_read}, süre: {report.seconds:.2f} sn")
        _listed("Tutarsızlıklar", [
            f"{issue.invariant} {issue.kind}"
            + (f" maç {issue.event_id}" if issue.event_id is not None else "")
            + (f" {issue.path}" if issue.path else "")
            + (f": {issue.detail}" if issue.detail else "")
            + (" [onarıldı]" if issue.repaired else "")
            for issue in report.issues
        ])
        _listed("Yarım kalmış geçici dosyalar" + (" (silindi)" if args.repair else ""), report.leftovers)
        _listed("Okunamayan ya da tanınmayan girdiler", _problem_lines(report))
        _listed("Kullanılmayan eski kopyalar", _superseded_lines(report))
        print("Katalog dosyalarla tutarlı." if report.ok else
              f"{len(report.open_issues)} tutarsızlık giderilmedi (verify --repair ya da rebuild).")
    return EXIT_OK if report.ok else EXIT_ISSUES


def run_stats(admin: CatalogAdmin, args: argparse.Namespace, store: Optional[Store] = None) -> int:
    stats = admin.stats()
    if args.json:
        _emit(stats)
        return EXIT_OK
    print(f"Katalog: {stats['path']}")
    if not stats["exists"]:
        print("  dosya yok (rebuild ile kurulur)")
        return EXIT_OK
    print(f"  boyut: {stats['size_bytes']} bayt, şema: {stats['schema_version']}, "
          f"türetme sürümü: {stats['derive_version']}")
    if not stats["usable"]:
        print(f"  kullanılamıyor: {stats['rebuild_reason']} (rebuild gerekli)")
    for title, key in (("Tablolar", "tables"), ("Bilgi", "meta")):
        if key in stats:
            print(f"{title}:")
            for name, value in stats[key].items():
                print(f"  {name}: {value}")
    for section in ("events", "slices"):
        for name, value in stats.get(section, {}).items():
            print(f"{section}.{name}: {value}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kataloğu (catalog.db) yeniden kurar, uzlaştırır, doğrular, sayımlarını gösterir")
    parser.add_argument("--data-dir", default=None, help='veri dizini (varsayılan: DATA_DIR ortam değişkeni, yoksa "data")')
    commands = parser.add_subparsers(dest="command", required=True)

    rebuild = commands.add_parser("rebuild", help="kataloğu dosyalardan yeniden kurar")
    rebuild.add_argument("--mode", choices=(MODE_AUTO, MODE_IN_PLACE, MODE_RECREATE), default=MODE_AUTO,
                         help="auto: şema uyuyorsa yerinde, yoksa dosyayı yeniden yaratarak")
    rebuild.set_defaults(run=run_rebuild)

    reconcile = commands.add_parser("reconcile", help="kataloğu, arkasından değişen dosyalarla yeniden eşitler")
    reconcile.add_argument("--deep", action="store_true",
                           help="imzalara bakmaz: her maçı ve her listeyi yeniden okur, sonra doğrular ve onarır")
    reconcile.add_argument("--v3", action="store_true", help="v3 maç dizinlerini de imzalarıyla karşılaştırır")
    reconcile.set_defaults(run=run_reconcile)

    verify = commands.add_parser("verify", help="kataloğu dosyalarla karşılaştırır")
    verify.add_argument("--deep", action="store_true", help="her maçı ve v3 yüklerinin hepsini yeniden okur")
    verify.add_argument("--repair", action="store_true", help="tutmayan maçları dosyalardan yeniden dizinler")
    verify.set_defaults(run=run_verify)

    stats = commands.add_parser("stats", help="kataloğun halini ve satır sayılarını gösterir")
    stats.set_defaults(run=run_stats)

    for command in (rebuild, reconcile, verify, stats):
        command.add_argument("--json", action="store_true", help="çıktıyı JSON olarak yazar")
    return parser


def _is_store(data_dir: str) -> bool:
    """Dizin bir depo mu (`open_store(create=False)` ile açılabilir mi): schema.json ve state.db yerinde."""
    return all(os.path.isfile(layout.resolve(data_dir, name)) for name in (layout.SCHEMA_FILE, layout.STATE_DB))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir or os.environ.get("DATA_DIR") or "data"
    if not os.path.isdir(data_dir):
        print(f"Veri dizini yok: {data_dir}", file=sys.stderr)
        return EXIT_USAGE
    try:
        if _is_store(data_dir):
            # Katalog bulunduğu haliyle: açılıştaki otomatik kurma / uzlaştırma çalışmaz, komut kendisi yapar
            store = open_store(data_dir, create=False, sync_catalog=False)
            try:
                return int(args.run(store.catalog, args, store))
            finally:
                store.close()
        with CatalogAdmin(data_dir) as admin:
            return int(args.run(admin, args))
    except StoreError as exc:
        print(f"Depolama hatası: {exc}", file=sys.stderr)
        return EXIT_STORAGE


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):  # UTF-8 olmayan uçbirimde (Windows) Türkçe harf çıktıyı düşürmesin
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="backslashreplace")
    sys.exit(main())
