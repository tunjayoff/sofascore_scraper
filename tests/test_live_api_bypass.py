"""
Canlı API testi — Sofascore API'ye gerçek istek atarak bypass'ın çalışıp çalışmadığını doğrular.
Bu test ağ erişimi gerektirir.
"""
import asyncio
import sys
import os

# Proje kök dizinini path'e ekle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils import (
    make_api_request,
    create_session_async,
    make_api_request_async,
    get_request_headers,
    IMPERSONATE_PROFILES,
)


def test_sync_live_request():
    """Senkron istek ile Premier League sezonlarını çek."""
    print("\n" + "=" * 60)
    print("TEST 1: Senkron API İsteği (Premier League sezonları)")
    print("=" * 60)
    
    url = "/unique-tournament/17/seasons"
    data = make_api_request(url, max_retries=2, timeout=15)
    
    if data and "seasons" in data:
        seasons = data["seasons"]
        print(f"  ✅ BAŞARILI — {len(seasons)} sezon alındı")
        for s in seasons[:3]:
            print(f"     - {s.get('name', '?')} (ID: {s.get('id', '?')})")
        return True
    else:
        print(f"  ❌ BAŞARISIZ — Yanıt: {data}")
        return False


async def test_async_live_request():
    """Asenkron istek + warm-up ile LaLiga sezonlarını çek."""
    print("\n" + "=" * 60)
    print("TEST 2: Asenkron API İsteği + Warm-up (LaLiga sezonları)")
    print("=" * 60)
    
    url = "/unique-tournament/8/seasons"
    async with create_session_async() as session:
        data = await make_api_request_async(session, url, max_retries=2)
    
    if data and "seasons" in data:
        seasons = data["seasons"]
        print(f"  ✅ BAŞARILI — {len(seasons)} sezon alındı (warm-up ile)")
        for s in seasons[:3]:
            print(f"     - {s.get('name', '?')} (ID: {s.get('id', '?')})")
        return True
    else:
        print(f"  ❌ BAŞARISIZ — Yanıt: {data}")
        return False


def test_headers_display():
    """Üretilen header'ları göster."""
    print("\n" + "=" * 60)
    print("TEST 3: Header Çeşitlendirme Kontrolü")
    print("=" * 60)
    
    for i in range(3):
        headers = get_request_headers()
        print(f"\n  Set {i+1}:")
        for k, v in headers.items():
            print(f"    {k}: {v}")
    
    print(f"\n  Aktif profiller: {IMPERSONATE_PROFILES}")
    return True


def main():
    print("\n🔬 Sofascore API Bypass Canlı Test")
    print("=" * 60)
    
    results = []
    
    # Test 1: Header'lar (ağ gerektirmez)
    results.append(("Header Çeşitlendirme", test_headers_display()))
    
    # Test 2: Senkron canlı istek
    results.append(("Senkron API İsteği", test_sync_live_request()))
    
    # Test 3: Asenkron canlı istek + warm-up
    results.append(("Asenkron + Warm-up", asyncio.run(test_async_live_request())))
    
    # Sonuçlar
    print("\n" + "=" * 60)
    print("📊 SONUÇLAR")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"  {status} — {name}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print("🎉 Tüm canlı testler başarılı! API bypass çalışıyor.")
    else:
        print("⚠️  Bazı testler başarısız oldu.")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
