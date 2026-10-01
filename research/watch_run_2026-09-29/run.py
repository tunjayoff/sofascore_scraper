"""Talimat 03-B kabul koşusu: 3 spor x 10 maç, 2 saat; tek süreç, ortak 1 istek/sn sınırı."""
import collections
import json
import signal
import sys
import threading
import time
sys.path.insert(0, "/home/tunjayoff/Desktop/OwnProjects/sofascore_scraper")
from src.watcher import MatchWatcher, LIST_INTERVAL_SECONDS

DATA = "/tmp/sofascore-research/watch_run/data"
picked = json.load(open("/tmp/sofascore-research/watch_run/picked.json"))
lock = threading.Lock()
last = [0.0]
counts = collections.Counter()

def shared_fetch(path):
    with lock:
        wait = 1.0 - (time.monotonic() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()
    counts["total"] += 1
    counts["live" if path.endswith("/events/live") else "event"] += 1
    return MatchWatcher._default_fetch(path)

stop = [False]
signal.signal(signal.SIGTERM, lambda *a: stop.__setitem__(0, True))
watchers = [MatchWatcher(s, event_ids=ids, data_dir=DATA, fetch_json=shared_fetch) for s, ids in picked.items()]
t_start = time.time()
print("start", time.strftime("%H:%M:%S", time.gmtime()), flush=True)
for w in watchers:
    w.start()
until = t_start + 2 * 3600
while not stop[0] and time.time() < until:
    t0 = time.time()
    for w in watchers:
        if w.active_ids():
            w.tick()
    active = sum(len(w.active_ids()) for w in watchers)
    print(time.strftime("%H:%M:%S", time.gmtime()), "active", active, "requests", dict(counts),
          "interval", [w.event_interval for w in watchers], flush=True)
    if active == 0:
        break
    time.sleep(max(0.0, LIST_INTERVAL_SECONDS - (time.time() - t0)))
json.dump({"started": t_start, "ended": time.time(), "requests": dict(counts),
           "per_sport": {w.sport: w.requests for w in watchers}}, open(DATA + "/run_summary.json", "w"))
print("done", dict(counts), flush=True)
