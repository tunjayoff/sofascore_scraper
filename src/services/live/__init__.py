"""
Canlı izleme servisi (docs/design/02-services.md bölüm 8; plan maddesi P23).

    reducer      saf indirgeyici: maçın son bilinen durumu + gözlem → yeni durum + olaylar; olayların `live`
                 akışındaki biçimi (`data`, yinelenme anahtarı)
    poll_source  yoklama kaynağı: spor başına canlı liste ve gereken maç sayfaları
    supervisor   LiveService: `live` kilidi, kapsam (takipler), gözetim, engellenmede bekleme, bitiş onayı,
                 meşgul depoda yeniden deneme, günlük budama, durum bilgisi (`live_status`)
    push_source  sayfa dinleme kaynağı (P24, `--source page`, varsayılan): PageSource, PushFeed
    arbiter      spor başına kaynak hakemi (P24): SportArbiter; push sessizse yoklamaya döner
    direct_source doğrudan push istemcisi (P31, `--source direct`, uyarılı açık seçim): DirectSource

`ssc watch` (src/cli/commands/watch.py) servisi ön planda çalıştırır; eski `main.py --watch` takma adı
src/watcher.py üzerinden aynı indirgeyiciyi ve kaynağı kullanır. Canlı verinin HTTP uç noktası yoktur: olaylar
olay günlüğüne yazılır ve sink'lerle (stdout, dosya, webhook) ya da `ssc events` ile okunur.
"""
from __future__ import annotations

from typing import Any

_LAZY = {
    "LiveService": "src.services.live.supervisor",
    "LiveScope": "src.services.live.supervisor",
    "SportScope": "src.services.live.supervisor",
    "LiveReport": "src.services.live.supervisor",
    "Blocked": "src.services.live.supervisor",
    "explicit_scope": "src.services.live.supervisor",
    "scope_from_follows": "src.services.live.supervisor",
    "live_status": "src.services.live.supervisor",
    "PollSource": "src.services.live.poll_source",
    "Observation": "src.services.live.reducer",
    "reduce": "src.services.live.reducer",
    "PageSource": "src.services.live.push_source",
    "PushFeed": "src.services.live.push_source",
    "BrowserPageOpener": "src.services.live.push_source",
    "SportArbiter": "src.services.live.arbiter",
    "DirectSource": "src.services.live.direct_source",
    "DirectConnection": "src.services.live.direct_source",
    "BrowserCredentialReader": "src.services.live.direct_source",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


__all__ = sorted(_LAZY)
