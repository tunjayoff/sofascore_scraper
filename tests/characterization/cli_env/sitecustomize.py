"""
CLI goldenlarının alt süreç kurulumu: `python main.py ...` gerçek bir süreçte, ama ağa çıkmadan çalışır.

Python her açılışta `sitecustomize` adlı modülü (sys.path'te varsa) kendiliğinden içe aktarır. Bu dizin
yalnızca tests/characterization/test_cli_goldens.py tarafından alt sürecin PYTHONPATH'ine eklenir; hiçbir
üretim dosyası değişmez. Etkinleştiren ortam değişkenleri yoksa modül hiçbir şey yapmaz.

Ne yapar:
  - İstek katmanının modülleri (tests/fakes/sofascore.py: REQUEST_LAYER_MODULES) uygulama tarafından içe
    aktarıldığı ANDA sahte taşıyıcıyı (G-01) kurar. Daha önce kurmaz: `main.py`nin içe aktarma sırası
    (.env'in yüklenmesi, --version / --doctor'ın ağır modüllerden önce yanıtlanması) olduğu gibi kalır.
  - Tarayıcı köprüsü (sofascore_scraper/client/bridge.py) içe aktarılırsa gerçek tarayıcının başlatılmasını engeller
    (tests/conftest.py'deki `_isolate_request_layer` ile aynı kural).
  - Async oturum açıkken yazılan çıktıyı iki işaretin arasına alır (sırası sözleşme olmayan bölüm).
  - Süreç kapanırken iki dosya yazar: `transport.json` (sahtenin kaydı: istekler, beklemeler, oturumlar;
    yalnızca sahte kurulduysa) ve `process.json` (sahte kuruldu mu, hangi `sofascore_scraper.*` modülleri yüklendi).

Ortam değişkenleri (okunduktan sonra süreç ortamından silinir: uygulama ve onun alt süreçleri görmez):
  SOFASCORE_CLI_GOLDEN_WORLD          sahte dünyanın JSON dosyası (FakeSofaScore.from_file)
  SOFASCORE_CLI_GOLDEN_OUT            kayıtların yazılacağı dizin
  SOFASCORE_CLI_GOLDEN_REQUEST_LAYER  virgülle ayrılmış modül adları (testin REQUEST_LAYER_MODULES değeri)
"""
from __future__ import annotations

import atexit
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

WORLD_ENV = "SOFASCORE_CLI_GOLDEN_WORLD"
OUT_ENV = "SOFASCORE_CLI_GOLDEN_OUT"
REQUEST_LAYER_ENV = "SOFASCORE_CLI_GOLDEN_REQUEST_LAYER"

TRANSPORT_LOG = "transport.json"
PROCESS_LOG = "process.json"

# stdout'a ve stderr'e yazılan işaretler (bkz. _mark_concurrent_output); test bunları çıktıdan ayıklar
CONCURRENT_BEGIN = "<<cli-golden:concurrent-begin>>"
CONCURRENT_END = "<<cli-golden:concurrent-end>>"

BRIDGE_MODULE = "sofascore_scraper.client.bridge"  # tarayıcı köprüsü (P24)
_FAKE_MODULE_NAME = "_cli_golden_fake_sofascore"
_FAKE_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "fakes", "sofascore.py"
)


class _State:
    def __init__(self) -> None:
        self.world = ""
        self.out_dir = ""
        self.request_layer: List[str] = []
        self.loading: Set[str] = set()  # istek katmanının o anda yüklenmekte olan modülleri
        self.installing = False  # sahtenin kurulumu başladı (bir kez kurulur)
        self.fake: Any = None


_state = _State()


class _AfterImportLoader(importlib.abc.Loader):
    """Asıl yükleyiciyi sarar: modülün gövdesi hatasız çalıştıktan sonra `after(modül)` çağrılır."""

    def __init__(self, inner: Any, after: Callable[[Any], None], track: bool) -> None:
        self._inner = inner
        self._after = after
        self._track = track

    def create_module(self, spec: Any) -> Any:
        return self._inner.create_module(spec)

    def exec_module(self, module: Any) -> None:
        name = module.__name__
        # Modül, sarmalayıcı hiç olmamış gibi görünsün (izleme çıktısı, linecache, yeniden yükleme)
        module.__loader__ = self._inner
        if module.__spec__ is not None:
            module.__spec__.loader = self._inner
        if self._track:
            _state.loading.add(name)
        try:
            self._inner.exec_module(module)
        finally:
            _state.loading.discard(name)
        self._after(module)


class _AfterImportFinder(importlib.abc.MetaPathFinder):
    """İzlenen modüllerin bulunmasını standart bulucuya bırakır, yalnızca yükleyicilerini sarar."""

    def __init__(self, request_layer: Sequence[str]) -> None:
        self._request_layer = frozenset(request_layer)

    def find_spec(self, fullname: str, path: Optional[Sequence[str]], target: Any = None) -> Any:
        if fullname in self._request_layer:
            after, track = _request_layer_imported, True
        elif fullname == BRIDGE_MODULE:
            after, track = _bridge_imported, False
        else:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.loader is None:
            return spec
        spec.loader = _AfterImportLoader(spec.loader, after, track)
        return spec


def _load_fake_module() -> Any:
    """tests/fakes/sofascore.py'yi dosya yolundan yükler: tests/ dizini alt sürecin sys.path'ine girmez."""
    spec = importlib.util.spec_from_file_location(_FAKE_MODULE_NAME, _FAKE_SOURCE)
    if spec is None or spec.loader is None:
        raise ImportError(f"fake transport not found: {_FAKE_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_FAKE_MODULE_NAME] = module  # dataclass'lar modülünü sys.modules'ta arar
    spec.loader.exec_module(module)
    return module


def _request_layer_imported(module: Any) -> None:
    """
    İstek katmanının bir modülü yüklendi. Listedeki başka bir modül hâlâ yükleniyorsa (biri diğerini içe
    aktarıyorsa) en dıştaki bitene kadar beklenir: sahte, yarım yüklenmiş bir modülü yamalayamaz.
    """
    if _state.installing or _state.loading:
        return
    # Kurulum, listedeki henüz yüklenmemiş modülleri kendisi içe aktarır; onların yüklenmesi buraya yeniden
    # düşer. İkinci bir sahte kurulmamalı: istekler iki ayrı kayda bölünürdü.
    _state.installing = True
    fake = _load_fake_module().FakeSofaScore.from_file(_state.world)
    _mark_concurrent_output(fake)
    _state.fake = fake.install()


def _mark_concurrent_output(fake: Any) -> None:
    """
    İlk async oturum açılırken ve sonuncusu kapanırken stdout'a ve stderr'e bir işaret yazar. Sahtenin istek kaydındaki
    kural (FakeSofaScore.canonical_log) çıktıya da uygulanabilsin diye: bir oturum açıkken yapılan işlerin
    (eşzamanlı görevler, to_thread ile çalışan sync istekler) sırası sözleşme değildir; test, iki işaret
    arasındaki satırları sıralayarak karşılaştırır. İşaretler satır sonu içermez: çıktının satırlarını bozmaz.

    Sahtenin oturum açılış/kapanış bildirimleri (`_open_session` / `_close_session`) sarılır; bu adlar
    değişirse burada AttributeError ile hemen görülür.
    """
    open_session, close_session = fake._open_session, fake._close_session
    depth = 0

    def opened(impersonate: Optional[str]) -> int:
        nonlocal depth
        if depth == 0:
            # İki akışa da: P19'dan beri log satırları stderr'dedir ve eşzamanlı görevlerin satırları orada da sıralanır
            sys.stdout.write(CONCURRENT_BEGIN)
            sys.stderr.write(CONCURRENT_BEGIN)
        depth += 1
        return int(open_session(impersonate))

    def closed(number: int) -> None:
        nonlocal depth
        close_session(number)
        if depth > 0:
            depth -= 1
            if depth == 0:
                sys.stdout.write(CONCURRENT_END)
                sys.stderr.write(CONCURRENT_END)

    fake._open_session, fake._close_session = opened, closed


def _bridge_imported(module: Any) -> None:
    """Tarayıcı köprüsü yüklendi: gerçek tarayıcı başlatılamaz (403 challenge yolu yanlışlıkla açılırsa)."""

    async def _no_real_browser(self: Any) -> None:
        raise RuntimeError("CLI golden subprocess must not launch a real browser")

    module.BrowserBridge._launch = _no_real_browser


def _save_logs() -> None:
    """Süreç kapanırken: sahtenin kaydı ve sürecin özeti. En önce kaydedildiği için en son çalışır."""
    try:
        os.makedirs(_state.out_dir, exist_ok=True)
        if _state.fake is not None:
            _state.fake.save_log(os.path.join(_state.out_dir, TRANSPORT_LOG))
        process: Dict[str, Any] = {
            "fake_installed": _state.fake is not None,
            "src_modules": sorted(name for name in sys.modules if name == "sofascore_scraper" or name.startswith("sofascore_scraper.")),
        }
        with open(os.path.join(_state.out_dir, PROCESS_LOG), "w", encoding="utf-8") as f:
            json.dump(process, f, ensure_ascii=False, indent=1)
    except Exception as exc:  # kapanışta hata: test, eksik dosyadan anlar; nedeni stderr'de kalsın
        print(f"cli golden sitecustomize: could not save logs: {exc!r}", file=sys.stderr)


def _activate() -> None:
    world = os.environ.pop(WORLD_ENV, "")
    out_dir = os.environ.pop(OUT_ENV, "")
    request_layer = [name for name in os.environ.pop(REQUEST_LAYER_ENV, "").split(",") if name]
    if not (world and out_dir and request_layer):
        return
    _state.world, _state.out_dir, _state.request_layer = world, out_dir, request_layer
    sys.meta_path.insert(0, _AfterImportFinder(request_layer))
    atexit.register(_save_logs)


_activate()
