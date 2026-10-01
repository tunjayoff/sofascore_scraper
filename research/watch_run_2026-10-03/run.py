"""
Tek sporun izleyicisi (start.sh her spor için ayrı süreç başlatır).

picked.json'daki maçları event_ids modunda --hours boyunca izler; olaylar <out>/watch_events.jsonl
(üç süreç aynı dosyaya satır ekler), durum <out>/watch_state_{sport}.json. Bitince her seçilen
maçın son durumu bir kez daha okunur (final_{sport}.json: takılı olayların gerçekten takılı olup
olmadığı buradan anlaşılır) ve istek sayaçları run_summary_{sport}.json'a yazılır.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import signal
import time

from _common import Client, fetch_json, log_requests, utc_now, write_json

from src.status import classify_status
from src.watcher import MatchWatcher


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", type=float, default=2.0)
    args = ap.parse_args()

    ids = json.load(open(os.path.join(args.out, "picked.json")))[args.sport]
    log_requests(os.path.join(args.out, "requests.log"), args.sport)
    client = Client()
    counts: collections.Counter = collections.Counter()

    def fetch(path: str):
        counts["live" if path.endswith("/events/live") else "event"] += 1
        return fetch_json(client, path)

    watcher = MatchWatcher(args.sport, event_ids=ids, data_dir=args.out, fetch_json=fetch)
    signal.signal(signal.SIGTERM, lambda *_: watcher.stop())
    started = time.time()
    ended = None
    print(args.sport, "başladı", utc_now(), len(ids), "maç", flush=True)
    try:
        watcher.run(until_seconds=args.hours * 3600)
        ended = time.time()
        final = {}
        for eid in ids:
            event = (fetch(f"/event/{eid}") or {}).get("event") or {}
            st = event.get("status") or {}
            final[str(eid)] = {
                "class": classify_status(event).value if event else None,
                "type": st.get("type"),
                "code": st.get("code"),
                "description": st.get("description"),
                "start_ts": event.get("startTimestamp"),
            }
        write_json(os.path.join(args.out, f"final_{args.sport}.json"), {"at_utc": utc_now(), "events": final})
    finally:
        client.close()
        write_json(
            os.path.join(args.out, f"run_summary_{args.sport}.json"),
            {
                "sport": args.sport,
                "started": started,
                "ended": ended or time.time(),
                "completed_normally": ended is not None,
                "watcher_calls": dict(counts),
                "http_requests": client.requests,
                "by_status": client.by_status,
            },
        )
        print(args.sport, "bitti", utc_now(), "istek", client.requests, flush=True)


if __name__ == "__main__":
    main()
