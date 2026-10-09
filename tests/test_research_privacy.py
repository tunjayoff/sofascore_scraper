"""
FX-29c: kayıtlı araştırma verisi (research/**) istemcinin ya da push sunucularının adresini, istemcinin konumunu
taşımaz.

2026-10-09 canlı koşusunda (lv-20261009) keşif tarayıcısı sayfanın çağırdığı `/api/v1/country/alpha2` yanıtını
(istemcinin IP'si, şehri, bölgesi, TLS parmak izi) örnek olarak, NATS INFO karelerinin connect_urls alanını (push
sunucularının adresleri) ws.jsonl'e olduğu gibi yazmıştı; ikisi de elle silindi. Tarayıcı artık yazmadan maskeler
(scripts/explore_all_sports.py, `redact`); bu test commit edilmiş veriyi tarar:
  - belgeleme (RFC 5737 / RFC 3849) ve özel/yerel aralıklar dışında IPv4 ya da IPv6 adresi yok;
  - hiçbir JSON belgesinde (JSONL satırı ve JSON taşıyan dizgi alanları dahil) istemci konumu anahtarlarının
    (GEO_KEYS) değeri maskesiz değil. Stadyum bilgisi (`venue` altı: city, venueCoordinates) herkese açıktır, hariç.

Sürüm benzeri dizgiler yanlış alarm vermesin diye IPv4 dört noktalı sekizli ister (her biri 0-255, bitişiğinde
harf/rakam yok, daha uzun bir noktalı sayı dizisinin parçası değil: 1.2.3.4.5 geçmez); IPv6 adayı ipaddress ile
doğrulanır (12:30:45 gibi saatler geçmez). Gerçek veride gerekçeli bir istisna gerekirse IPV4_ALLOWLIST /
IPV6_ALLOWLIST'e adresi ve nedeni yazılır.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(ROOT, "research")
SUFFIXES = (".json", ".jsonl", ".md", ".txt")
REDACTED = "<redacted>"

# scripts/explore_all_sports.py GEO_KEYS ile aynı küme (bilerek ayrı yazıldı: test, script'ten bağımsız denetler)
GEO_KEYS = frozenset({"ip", "city", "region_code", "f", "client_ip", "postal", "latitude", "longitude", "lat", "lon",
                      "geo"})
GEO_KEEP_UNDER = frozenset({"venue"})

ALLOWED_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    # belgeleme
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32",
    # özel, yerel, döngü, belirsiz
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "0.0.0.0/32",
    "fc00::/7", "fe80::/10", "::1/128", "::/128",
))
# adres -> neden (gerçek veride meşru bir dört sekizli dizgi çıkarsa)
IPV4_ALLOWLIST: Dict[str, str] = {}
IPV6_ALLOWLIST: Dict[str, str] = {}

IPV4_RE = re.compile(r"(?<!\w)(?<!\d\.)(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\w|\.\d)")
IPV6_CANDIDATE_RE = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}(?:(?:\d{1,3}\.){3}\d{1,3}|[0-9A-Fa-f]{1,4})?"
                               r"(?:%[\w.-]+)?(?![\w:])")


def research_files(root: str = RESEARCH) -> Iterator[str]:
    for base, _, files in os.walk(root):
        for name in sorted(files):
            if name.endswith(SUFFIXES):
                yield os.path.join(base, name)


def _allowed(addr: Any, allowed: Sequence[Any]) -> bool:
    return any(addr.version == net.version and addr in net for net in allowed)


def ip_literals(text: str, allowed: Sequence[Any] = ALLOWED_NETWORKS) -> List[str]:
    """Metindeki izin verilen aralıklar ve listeler dışındaki IPv4/IPv6 adresleri."""
    found = []
    for m in IPV4_RE.finditer(text):
        if any(int(o) > 255 for o in m.groups()) or m.group() in IPV4_ALLOWLIST:
            continue
        if not _allowed(ipaddress.IPv4Address(m.group()), allowed):
            found.append(m.group())
    if ":" in text:
        for m in IPV6_CANDIDATE_RE.finditer(text):
            cand = m.group().split("%", 1)[0]
            if not re.search(r"[0-9A-Fa-f]", cand) or cand in IPV6_ALLOWLIST:
                continue
            try:
                addr = ipaddress.IPv6Address(cand)
            except ValueError:
                continue
            if not _allowed(addr.ipv4_mapped or addr, allowed):
                found.append(cand)
    return found


def _json_string(v: str) -> Optional[Any]:
    """JSON taşıyan dizgi alanı (ws.jsonl'de INFO gövdesi dizgi olarak saklanır)."""
    s = v.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        return json.loads(s)
    except ValueError:
        return None


def geo_leaks(o: Any, path: str = "", keep: bool = False) -> List[Tuple[str, str]]:
    """Maskesiz istemci konumu değerleri: (yol, değerin türü). Değerin kendisi rapora yazılmaz."""
    out: List[Tuple[str, str]] = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k in GEO_KEYS and not keep and v is not None and v != REDACTED:
                out.append((f"{path}/{k}", type(v).__name__))
            else:
                out.extend(geo_leaks(v, f"{path}/{k}", keep or k in GEO_KEEP_UNDER))
    elif isinstance(o, list):
        for v in o:
            out.extend(geo_leaks(v, f"{path}[]", keep))
    elif isinstance(o, str):
        inner = _json_string(o)
        if inner is not None:
            out.extend(geo_leaks(inner, path + "<json>", keep))
    return out


def json_documents(path: str, text: str) -> Iterator[Tuple[str, Any]]:
    if path.endswith(".json"):
        try:
            yield "", json.loads(text)
        except ValueError:
            return
    elif path.endswith(".jsonl"):
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    yield f":{n}", json.loads(line)
                except ValueError:
                    continue


def scan(root: str = RESEARCH) -> Dict[str, List[str]]:
    """Kök altındaki bulgular: {"ipv4", "ipv6", "geo"} -> dosya yolu ve konum listesi (adresin kendisi yazılmaz)."""
    found: Dict[str, List[str]] = {"ipv4": [], "ipv6": [], "geo": []}
    for path in research_files(root):
        rel = os.path.relpath(path, os.path.dirname(root))
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        ips = ip_literals(text)
        for kind, hits in (("ipv4", [ip for ip in ips if ":" not in ip]), ("ipv6", [ip for ip in ips if ":" in ip])):
            if hits:
                found[kind].append(f"{rel}: {len(hits)} occurrence(s), {len(set(hits))} distinct")
        for where, doc in json_documents(path, text):
            for key_path, kind in geo_leaks(doc):
                found["geo"].append(f"{rel}{where} {key_path} ({kind})")
    return found


@pytest.fixture(scope="module")
def findings():
    if not os.path.isdir(RESEARCH):
        pytest.skip("no research tree")
    return scan()


def test_research_tree_has_no_real_ipv4_addresses(findings):
    assert not findings["ipv4"], "public IPv4 addresses in research/:\n" + "\n".join(findings["ipv4"])


def test_research_tree_has_no_real_ipv6_addresses(findings):
    assert not findings["ipv6"], "public IPv6 addresses in research/:\n" + "\n".join(findings["ipv6"])


def test_research_tree_has_no_unredacted_client_geo_values(findings):
    assert not findings["geo"], "unredacted client location values:\n" + "\n".join(findings["geo"][:50])


# --- denetleyicinin kendisi (sahte değerler) -------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "version 1.2.3", "2.29.1", "1.2.3.4.5", "v10.20.30.40.1", "256.1.1.1", "kickoff 12:30:45", "10:00",
    "std::string", "::", "Foo::Bar", "ratio 1.25", "1790856215.508",
])
def test_detector_ignores_version_like_and_time_strings(text):
    assert ip_literals(text, allowed=()) == []


@pytest.mark.parametrize("text, expected", [
    ('{"ip": "192.0.2.10"}', ["192.0.2.10"]),
    ('"connect_urls": ["198.51.100.7:9222", "[2001:db8:0:1::]:9222"]', ["198.51.100.7", "2001:db8:0:1::"]),
    ("host=203.0.113.5.", ["203.0.113.5"]),
    ("SUB host.192.0.2.20 1", ["192.0.2.20"]),
    ("::ffff:192.0.2.1", ["192.0.2.1", "::ffff:192.0.2.1"]),
])
def test_detector_finds_addresses(text, expected):
    assert ip_literals(text, allowed=()) == expected  # izin verilen aralık olmadan belgeleme adresleri de yakalanır
    assert ip_literals(text) == []  # belgeleme aralığı varsayılan olarak izinli


def test_geo_detector_respects_redaction_and_venue():
    doc = {
        "alpha2": "XX", "country": "Exampleland", "ip": REDACTED, "city": REDACTED, "f": None,
        "event": {"venue": {"city": {"name": "Exampleville"}, "venueCoordinates": {"latitude": 1.5, "longitude": 2.5}}},
    }
    assert geo_leaks(doc) == []
    leaked = {"body": {"region_code": "EX", "nested": [{"geo": {"lat": 1.5}}]},
              "info": json.dumps({"client_ip": "192.0.2.10"})}
    assert geo_leaks(leaked) == [("/body/region_code", "str"), ("/body/nested[]/geo", "dict"),
                                 ("/info<json>/client_ip", "str")]
