"""
Spor başına N canlı maç seçer → picked.json (+ seçim anındaki durumlarıyla picked_detail.json).

Kural: status.type == inprogress; en yeni başlayan önce (2 saatte bitme ihtimali yüksek,
koşunun tamamına yayılır). Futbolda son 30 dk, basketbolda son 40 dk içinde başlayanlar
önceliklidir; yetmezse kalan canlı maçlar aynı sırayla eklenir. Tenis startTimestamp'i
planlanan saattir, süre sınırı yoktur.
"""
from __future__ import annotations

import argparse
import os
import time

from _common import SPORTS, Client, fetch_json, log_requests, utc_now, write_json

FRESH_MINUTES = {"football": 30, "basketball": 40, "tennis": None}


def pick(events: list, sport: str, n: int, now: float) -> list:
    live = [e for e in events if (e.get("status") or {}).get("type") == "inprogress"]
    live.sort(key=lambda e: -(e.get("startTimestamp") or 0))
    limit = FRESH_MINUTES[sport]
    fresh = [e for e in live if limit is None or now - (e.get("startTimestamp") or 0) <= limit * 60]
    rest = [e for e in live if e not in fresh]
    return (fresh + rest)[:n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()

    log_requests(os.path.join(args.out, "requests.log"), "pick")
    client = Client()
    picked, detail = {}, {"at_utc": utc_now(), "sports": {}}
    try:
        for sport in SPORTS:
            data = fetch_json(client, f"/sport/{sport}/events/live") or {}
            chosen = pick(data.get("events") or [], sport, args.n, time.time())
            picked[sport] = [e["id"] for e in chosen]
            detail["sports"][sport] = {
                "live_in_list": len(data.get("events") or []),
                "picked": [
                    {
                        "id": e["id"],
                        "match": f"{(e.get('homeTeam') or {}).get('name')} – {(e.get('awayTeam') or {}).get('name')}",
                        "tournament": ((e.get("tournament") or {}).get("uniqueTournament") or {}).get("name"),
                        "start_ts": e.get("startTimestamp"),
                        "status": (e.get("status") or {}).get("description"),
                    }
                    for e in chosen
                ],
            }
            print(sport, len(picked[sport]), "maç seçildi", flush=True)
    finally:
        client.close()
    detail["requests"] = client.requests
    write_json(os.path.join(args.out, "picked.json"), picked)
    write_json(os.path.join(args.out, "picked_detail.json"), detail)


if __name__ == "__main__":
    main()
