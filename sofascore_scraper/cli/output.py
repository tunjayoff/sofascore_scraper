"""
CLI çıktı kuralları (docs/design/02-services.md bölüm 4.4).

  * stdout yalnızca sonucu taşır; loglar, uyarılar ve okunur hata metni stderr'e gider.
  * `--json` olmadan: insanlar için kısa metin, yerelleştirilmiş (locales/*.json).
  * `--json` ile: tam olarak bir JSON belgesi (zarf), hiçbir zaman yerelleştirilmez:

        {"ok": true,  "command": "...", "schema": "sofascore.cli/1", "version": "...", "data": {...}, "warnings": []}
        {"ok": false, "command": "...", "schema": "sofascore.cli/1", "version": "...",
         "error": {"code": "...", "message": "...", "details": {...}}, "exit_code": 2}

    `ok`, komutun çalışıp çalışmadığını söyler; sonucun durumu (`doctor`da başarısız denetim, ileride kısmi
    başarı) çıkış kodunda ve `data` içindedir. Hata zarfı da stdout'a yazılır: çağıran program tek bir akışı okur.
  * `--output ndjson`: akış üreten komutlar satır başına bir nesne yazar; tek seferlik komutlarda zarf tek
    satırdır.
  * Akış komutları (`events`, `jobs tail`; plan maddesi P19): komut `Output.begin_stream()` çağırır ve
    satırlarını `Output.line()` ile yazar; her satır `type` alanı olan bir nesnedir. Akış başladıktan sonra
    sonuç zarfı yazılmaz (uyarılar ve notlar yine stderr'e gider). Akışın ortasındaki bir hata stdout'a tek bir
    satır olarak yazılır, `{"type":"error","error":{code,message,details},"exit_code":N}`; okunur metni metin
    kipinde stderr'e de gider. Akışı okuyan süreç kapanırsa (`ssc events | head -1`) komut 0 ile biter:
    okuyan yeterince satır aldı. Tek seferlik bir komutun sonucu yazılamadıysa çıkış kodu 1'dir.

Alan kuralları: zaman damgaları ISO-8601 UTC ve `Z`; süreler saniye; enum'lar küçük harf ve alt çizgi;
olmayan değer `null`dır, boş dizge değil.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, TextIO

from sofascore_scraper.errors import PlatformError
from sofascore_scraper.version import __version__

SCHEMA = "sofascore.cli/1"

# Akış satırlarının ortak türleri: akış kendiliğinden bittiğinde son satır `end`, ortasında hata `error`
STREAM_END = "end"
STREAM_ERROR = "error"

TEXT = "text"
JSON = "json"
NDJSON = "ndjson"
OUTPUT_MODES = (TEXT, JSON, NDJSON)

# `t("anahtar", ad=değer)`: locales/*.json'daki metin; anahtar yoksa anahtarın kendisi döner
Translator = Callable[..., str]


@dataclass(frozen=True)
class CliWarning:
    """
    Hata olmayan, kullanıcının bilmesi gereken bir durum. `message` İngilizcedir (JSON çıktısı yerelleştirilmez).
    `logged`: aynı metin log satırı olarak stderr'e zaten yazıldı; metin kipinde ikinci kez yazılmaz.
    """

    code: str
    message: str
    logged: bool = False

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass
class CommandResult:
    """
    Bir komutun sonucu.

    data       JSON zarfının `data` alanı
    text       metin kipinde stdout'a yazılan, yerelleştirilmiş çıktı; None: hiçbir şey yazılmaz
    exit_code  sonucun durumu (0: başarı). Hatalar istisna olarak fırlatılır, burada bildirilmez
    warnings   zarfın `warnings` alanı; metin kipinde stderr'e yazılır
    notes      metin kipinde stderr'e yazılan, yerelleştirilmiş bilgi satırları (`--quiet` bunları kapatır)
    """

    data: Any = None
    text: Optional[str] = None
    exit_code: int = 0
    warnings: Sequence[CliWarning] = ()
    notes: Sequence[str] = ()


def success_envelope(command: Optional[str], data: Any, warnings: Sequence[CliWarning] = ()) -> Dict[str, Any]:
    return {
        "ok": True,
        "command": command,
        "schema": SCHEMA,
        "version": __version__,
        "data": data,
        "warnings": [warning.to_dict() for warning in warnings],
    }


def error_envelope(command: Optional[str], error: PlatformError, exit_code: int) -> Dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "schema": SCHEMA,
        "version": __version__,
        "error": error.to_dict(),
        "exit_code": exit_code,
    }


_ERROR_OBJECT: Dict[str, Any] = {
    "type": "object",
    "required": ["code", "message", "details"],
    "additionalProperties": False,
    "properties": {
        "code": {"type": "string", "description": "An error code of `describe errors`."},
        "message": {"type": "string", "description": "English, never localised."},
        "details": {"type": ["object", "null"]},
    },
}
_WARNING_OBJECT: Dict[str, Any] = {
    "type": "object",
    "required": ["code", "message"],
    "additionalProperties": False,
    "properties": {"code": {"type": "string"}, "message": {"type": "string"}},
}
_COMMON_PROPERTIES: Dict[str, Any] = {
    "command": {"type": ["string", "null"], "description": "The command path, e.g. \"config show\"; null when none was recognised."},
    "schema": {"const": SCHEMA},
    "version": {"type": "string", "description": "Application version."},
}

# `describe schemas` bunu yazdırır; tests/test_cli_skeleton.py her komutun çıktısını buna göre denetler.
ENVELOPE_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": SCHEMA,
    "title": "CLI result envelope",
    "description": "The one JSON document a command prints on stdout with --json.",
    "oneOf": [
        {
            "type": "object",
            "required": ["ok", "command", "schema", "version", "data", "warnings"],
            "additionalProperties": False,
            "properties": {
                "ok": {"const": True},
                **_COMMON_PROPERTIES,
                "data": {"description": "The result of the command; its shape depends on the command."},
                "warnings": {"type": "array", "items": _WARNING_OBJECT},
            },
        },
        {
            "type": "object",
            "required": ["ok", "command", "schema", "version", "error", "exit_code"],
            "additionalProperties": False,
            "properties": {
                "ok": {"const": False},
                **_COMMON_PROPERTIES,
                "error": _ERROR_OBJECT,
                "exit_code": {"type": "integer", "description": "The exit code of the process (`describe exit-codes`)."},
            },
        },
    ],
}


def _json_default(value: Any) -> Any:
    """JSON'a doğrudan çevrilemeyen değerler: kümeler sıralı liste, diğer her şey (Path, tarih) metin olur."""
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    return str(value)


def dumps(document: Any, mode: str = JSON) -> str:
    """
    JSON metni. `json`: girintili tek belge; `ndjson`: tek satır. `ensure_ascii`: konsolun kod sayfası ne
    olursa olsun ayrıştırılabilir çıktı (sofascore_scraper/doctor.py ile aynı karar).
    """
    if mode == NDJSON:
        return json.dumps(document, ensure_ascii=True, separators=(",", ":"), default=_json_default)
    return json.dumps(document, ensure_ascii=True, indent=2, default=_json_default)


def _tolerant(stream: TextIO) -> None:
    """cp1252 gibi konsollarda çevrilemeyen karakter (Türkçe harf, ok işareti) çökme yerine `?` olur."""
    try:
        stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        pass


@dataclass
class Output:
    """
    Sonucu ve hataları doğru akışa, doğru biçimde yazar.

    mode   text | json | ndjson
    t      çeviri işlevi (metin kipi için)
    quiet  metin kipinde bilgi satırlarını (notes) yazma
    prog   yardım ipuçlarında görünen komut adı
    """

    mode: str = TEXT
    t: Translator = field(default=lambda key, **_kwargs: key)
    quiet: bool = False
    prog: str = "ssc"
    stdout: Optional[TextIO] = None
    stderr: Optional[TextIO] = None
    streaming: bool = False

    @property
    def machine(self) -> bool:
        return self.mode in (JSON, NDJSON)

    # --- akış ----------------------------------------------------------------------------------

    def begin_stream(self) -> None:
        """
        Komut stdout'a satır satır yazacak (NDJSON): bundan sonra sonuç zarfı yazılmaz, hata bir akış satırı
        olur. `--json` (tek belge) bir akışla birlikte kullanılamaz; bunu komut denetler.
        """
        self.streaming = True

    def raw_stream(self) -> TextIO:
        """
        stdout'un kendisi: komut biçimi kendisi olan veriyi (ör. `export --out -` ile CSV) olduğu gibi yazar.
        Önce `begin_stream()` çağrılmalıdır: sonuç zarfı stdout'a karışmasın.
        """
        stream = self._out()
        _tolerant(stream)
        return stream

    def line(self, document: Mapping[str, Any]) -> None:
        """Akışa bir satır (tek satırlık JSON). Akış başlamamışsa başlatır."""
        self.streaming = True
        self.write(dumps(document, NDJSON))

    def _out(self) -> TextIO:
        return self.stdout if self.stdout is not None else sys.stdout

    def _err(self) -> TextIO:
        return self.stderr if self.stderr is not None else sys.stderr

    def write(self, text: str) -> None:
        """stdout'a bir sonuç satırı (ya da belgesi)."""
        stream = self._out()
        _tolerant(stream)
        stream.write(text if text.endswith("\n") else text + "\n")
        stream.flush()

    def info(self, text: str) -> None:
        """stderr'e bir satır: uyarı, bilgi, okunur hata metni."""
        stream = self._err()
        _tolerant(stream)
        stream.write(text if text.endswith("\n") else text + "\n")
        stream.flush()

    # --- sonuç ---------------------------------------------------------------------------------

    def result(self, command: Optional[str], result: CommandResult, *, always_json: bool = False) -> None:
        if self.streaming:
            # Satırlar yazıldı: zarf yok. Uyarılar ve notlar akışı bozmadan stderr'e gider
            for warning in result.warnings:
                if not warning.logged:
                    self.info(self.t("ssc_warning", message=warning.message))
            if not self.quiet:
                for note in result.notes:
                    self.info(note)
            return
        if self.machine or always_json:
            self.write(dumps(success_envelope(command, result.data, result.warnings), self.mode if self.machine else JSON))
            return
        for warning in result.warnings:
            if not warning.logged:
                self.info(self.t("ssc_warning", message=warning.message))
        if not self.quiet:
            for note in result.notes:
                self.info(note)
        if result.text is not None:
            self.write(result.text)

    # --- hata ----------------------------------------------------------------------------------

    def error(self, command: Optional[str], error: PlatformError, exit_code: int) -> None:
        if self.streaming:
            # Akışın ortasında: okuyan program satır türünden anlar; insan için metin stderr'e
            self.write(dumps({"type": STREAM_ERROR, "error": error.to_dict(), "exit_code": exit_code}, NDJSON))
            if not self.machine:
                for text in self.error_lines(error):
                    self.info(text)
            return
        if self.machine:
            self.write(dumps(error_envelope(command, error, exit_code), self.mode))
            return
        for line in self.error_lines(error):
            self.info(line)

    def error_lines(self, error: PlatformError) -> List[str]:
        """Metin kipinde stderr'e yazılan satırlar: yerelleştirilmiş başlık, ileti ve (varsa) ipucu."""
        key = "ssc_error_" + error.code
        label = self.t(key)
        if label == key:  # çevirisi olmayan kod: kodun kendisi
            label = error.code
        lines = [f"{label}: {error.message}"]
        details: Mapping[str, Any] = error.details or {}
        holder = details.get("holder")
        if isinstance(holder, Mapping):
            lines.append(self.t(
                "ssc_error_holder",
                pid=holder.get("pid") if holder.get("pid") is not None else "?",
                host=holder.get("host") or "?",
                purpose=holder.get("purpose") or "?",
                since=holder.get("since") or "?",
            ))
        usage = details.get("usage")
        if isinstance(usage, str) and usage.strip():
            lines.append(usage.strip())
        if error.code == "invalid_request":
            lines.append(self.t("ssc_hint_usage", prog=self.prog))
        elif error.code == "config_invalid":
            lines.append(self.t("ssc_hint_config", prog=self.prog))
        return lines


__all__ = [
    "ENVELOPE_SCHEMA",
    "JSON",
    "NDJSON",
    "OUTPUT_MODES",
    "SCHEMA",
    "STREAM_END",
    "STREAM_ERROR",
    "TEXT",
    "CliWarning",
    "CommandResult",
    "Output",
    "Translator",
    "dumps",
    "error_envelope",
    "success_envelope",
]
