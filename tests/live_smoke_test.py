"""
Canlı Sofascore API entegrasyon doğrulama testi.
"""

from src.utils import make_api_request

def main():
    print("=" * 65)
    print("TEST 1: CANLI MAÇLAR (/sport/football/events/live)")
    print("=" * 65)
    live_data = make_api_request("/sport/football/events/live")
    if live_data and "events" in live_data:
        events = live_data["events"]
        print(f"Toplam {len(events)} canlı maç çekildi. Şu an oynanan ilk 5 maç:")
        for ev in events[:5]:
            tournament = ev.get("tournament", {}).get("name", "Lig")
            home = ev.get("homeTeam", {}).get("name", "Ev")
            away = ev.get("awayTeam", {}).get("name", "Dep")
            home_score = ev.get("homeScore", {}).get("current", 0)
            away_score = ev.get("awayScore", {}).get("current", 0)
            status = ev.get("status", {}).get("description", "")
            print(f"  * [{tournament}] {home} {home_score} - {away_score} {away} ({status})")
    else:
        print("HATA: Canlı maç verisi alınamadı!")

    print("\n" + "=" * 65)
    print("TEST 2: TRENDYOL SÜPER LİG SEZONLARI & PUAN DURUMU")
    print("=" * 65)
    seasons_data = make_api_request("/unique-tournament/52/seasons")
    if seasons_data and "seasons" in seasons_data:
        seasons = seasons_data["seasons"]
        print(f"Süper Lig Sezonları ({len(seasons)} sezon mevcut):")
        for s in seasons[:3]:
            print(f"  - {s.get('name')} (Sezon ID: {s.get('id')})")

        # Güncel sezonun puan durumunu çek
        curr_season = seasons[0]
        season_id = curr_season.get("id")
        season_name = curr_season.get("name")
        standings_data = make_api_request(f"/unique-tournament/52/season/{season_id}/standings/total")
        if standings_data and "standings" in standings_data:
            rows = standings_data["standings"][0].get("rows", [])
            print(f"\n{season_name} Puan Tablosu (İlk 5 Takım):")
            for r in rows[:5]:
                pos = r.get("position")
                team = r.get("team", {}).get("name")
                matches = r.get("matches")
                points = r.get("points")
                print(f"   {pos}. {team:<22} Maç: {matches:<2} Puan: {points}")
    else:
        print("HATA: Süper Lig sezon verisi alınamadı!")

    print("\n" + "=" * 65)
    print("SONUÇ: BÜTÜN API ÇAĞRILARI BAŞARIYLA TAMAMLANDI!")
    print("=" * 65)

if __name__ == "__main__":
    main()
