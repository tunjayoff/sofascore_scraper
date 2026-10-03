"""
overrides.json'ı yazan taraf: arayüzden / API'den yapılan ayar değişiklikleri (docs/design/02-services.md
bölüm 4.3, karar D4).

Yükleyici (src/config/loader.py) `CONFIG_DIR/overrides.json`'ı `overrides` katmanı olarak okur: `.env`'in
üstünde, yapılandırma dosyasının, süreç ortamının ve bayrakların altında. Bu modül o dosyanın tek yazarıdır.
Belge yapılandırma dosyasının biçimindedir (`{"client": {"rate": 3}}`); listeler ([[follow]], [[sink]],
[[schedule.task]]) burada verilemez (karar D11). Bir sporun dilim seçimi farkı `slices.<spor>` anahtarıyla
yazılır: `{"slices.football": {"enable": [...], "disable": [...]}}` → `[slices.football]` (plan maddesi P27).

    write_overrides({"client.rate": 3, "display.language": None})

Değeri None olan anahtar belgeden silinir (alttaki katmanın değeri geçerli olur). Her değer yazılmadan önce
yükleyicinin kendi kuralıyla denetlenir (`loader.coerce`); dosya atomik yazılır, 0600 izinlidir (proxy adresi
parola taşıyabilir) ve ardından ayarlar yeniden yüklenir. Yeniden yükleme başarısız olursa dosya eski haline
döner ve önceki ayarlar yürürlükte kalır.

Bir değerin daha güçlü bir katmanda sabitlenmiş (kilitli) olup olmadığına bu modül bakmaz: dosyaya yazılan
ama etkisi olmayan bir değeri reddetmek çağıranın (ayarlar API'si) işidir.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from src.config import loader
from src.config import settings as model
from src.exceptions import ConfigError
from src.config_files import atomic_write_text, file_lock
from src.private_files import PRIVATE_FILE_MODE, restrict_permissions


def overrides_path(environ: Optional[Mapping[str, str]] = None) -> Path:
    """overrides.json'ın yeri: yükleyicinin okuduğu dosyanın aynısı (CONFIG_DIR / overrides.json)."""
    env = os.environ if environ is None else environ
    return (Path(env.get("SOFASCORE_CONFIG_DIR") or "config") / loader.OVERRIDES_FILE_NAME).absolute()


def split_key(key: str) -> Tuple[str, str]:
    """'client.rate' → ('client', 'rate'); bilinmeyen ya da dosyada yazılamayan anahtar ConfigError."""
    section, _, name = key.partition(".")
    f = model.SETTING_KEYS.get(section, {}).get(name)
    if f is None:
        raise ConfigError(f"{key}: unknown setting")
    if not f.metadata["in_file"]:
        raise ConfigError(f"{key}: this value is read from the environment only ({loader.env_name(key)})")
    return section, name


def checked_value(key: str, value: Any) -> Any:
    """Değeri anahtarın kuralıyla denetler ve JSON'a yazılacak biçimde döndürür; uymuyorsa ConfigError."""
    section, name = split_key(key)
    try:
        coerced = loader.coerce(model.SETTING_KEYS[section][name], value)
    except ValueError as e:
        raise ConfigError(f"{key}: {e}") from None
    return list(coerced) if isinstance(coerced, tuple) else coerced


def read_document(path: Optional[Path] = None) -> Dict[str, Any]:
    """Dosyadaki belge; dosya yoksa boş. Geçerli JSON nesnesi değilse ConfigError."""
    target = path or overrides_path()
    if not target.is_file():
        return {}
    try:
        with open(target, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except OSError as e:
        raise ConfigError(f"{target}: cannot read the overrides file: {e}") from e
    except ValueError as e:
        raise ConfigError(f"{target}: not valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise ConfigError(f"{target}: expected a JSON object")
    return doc


# [slices.<spor>]: bir sporun dilim seçimi farkı ({"enable": [...], "disable": [...]}); anahtarı `slices.<spor>`
SLICES_PREFIX = "slices."


def checked_slice_override(key: str, value: Any) -> Dict[str, Any]:
    """`slices.<spor>` değerini yükleyicinin kuralıyla denetler: {"enable": [...], "disable": [...]}; ConfigError."""
    sport = key[len(SLICES_PREFIX):]
    parsed = loader._parse_slices({sport: value}, "slices")[sport]
    return {"enable": list(parsed.enable), "disable": list(parsed.disable)}


def _merge_slice_override(document: Dict[str, Any], key: str, value: Any) -> None:
    sport = key[len(SLICES_PREFIX):]
    body = document.get("slices")
    body = document["slices"] = dict(body) if isinstance(body, dict) else {}
    if value is None:
        body.pop(sport, None)
    else:
        body[sport] = checked_slice_override(key, value)
    if not body:
        del document["slices"]


def merged(document: Mapping[str, Any], changes: Mapping[str, Any]) -> Dict[str, Any]:
    """Belgenin, değişiklikler uygulanmış kopyası (dosyaya dokunmaz). Boşalan bölüm belgeden çıkar."""
    out: Dict[str, Any] = {
        section: dict(body) if isinstance(body, dict) else body for section, body in document.items()
    }
    for key, value in changes.items():
        if key.startswith(SLICES_PREFIX):
            _merge_slice_override(out, key, value)
            continue
        section, name = split_key(key)
        body = out.get(section)
        if not isinstance(body, dict):
            body = out[section] = {}
        if value is None:
            body.pop(name, None)
        else:
            body[name] = checked_value(key, value)
        if not body:
            del out[section]
    return out


def _restore(path: Path, before: Optional[bytes]) -> None:
    if before is None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    else:
        atomic_write_text(str(path), before.decode("utf-8"))


def write_overrides(changes: Mapping[str, Any]) -> loader.LoadedSettings:
    """
    Değişiklikleri overrides.json'a yazar ve ayarları yeniden yükler; yeni ayarları döndürür.

    changes  'bölüm.anahtar' → değer; None anahtarı belgeden siler.

    ConfigError: bilinmeyen anahtar, kuralına uymayan değer ya da yeni belgeyle ayarlar kurulamıyor (dosya o
    durumda eski haline döner). OSError: dosya yazılamadı (hiçbir şey değişmedi).
    """
    path = overrides_path()
    with file_lock(str(path)):
        try:
            before: Optional[bytes] = path.read_bytes() if path.is_file() else None
        except OSError as e:
            raise ConfigError(f"{path}: cannot read the overrides file: {e}") from e
        document = merged(read_document(path), changes)
        atomic_write_text(str(path), json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        restrict_permissions(str(path), PRIVATE_FILE_MODE)
        try:
            return loader.reload()
        except Exception:
            _restore(path, before)
            try:
                loader.reload()
            except Exception:  # önceki ayarlar zaten yürürlükte; asıl hata aşağıda çıkar
                pass
            raise


__all__ = ["SLICES_PREFIX", "checked_slice_override", "checked_value", "merged", "overrides_path", "read_document",
           "split_key", "write_overrides"]
