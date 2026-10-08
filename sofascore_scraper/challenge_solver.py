"""
Eski ad: tarayıcı köprüsü P24 ile sofascore_scraper/client/bridge.py'ye taşındı.

Bu modül o modülün takma adıdır: `import sofascore_scraper.challenge_solver` aynı modül nesnesini verir. Böylece eski
import'lar, `patch("sofascore_scraper.challenge_solver.X")` ve `monkeypatch.setattr(cs, ...)` atamaları köprünün kendi
adlarına ulaşır (modül düzeyindeki token önbelleği ve arka plan döngüsü tek kopyadır).

Uygulamanın hiçbir modülü bu adı kullanmaz. Takma ad P30'da kalkacaktı; yalnızca FX-29'un araştırma betikleri
(scripts/explore_all_sports.py ve scripts/_research_common.py; testleri tests/test_research_explorer.py) bu adla
içe aktardığı için durur: o betikler `sofascore_scraper.client.bridge`e geçince silinir.

`BrowserBridge` adı bu gövdede de bağlanır: içe aktarma kancaları (tests/characterization/cli_env/
sitecustomize.py) yüklenen modül nesnesine bakar ve sınıfı yamalar; sınıf iki adda da aynı nesnedir.
"""
from __future__ import annotations

import sys

from sofascore_scraper.client import bridge as _bridge
from sofascore_scraper.client.bridge import BrowserBridge

sys.modules[__name__] = _bridge

__all__ = ["BrowserBridge"]
