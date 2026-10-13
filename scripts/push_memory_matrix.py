"""
S6: scripts/push_memory_probe.py'nin yapılandırmalarını SIRAYLA koşturur. İki koşu asla üst üste binmez: her koşu
bitmeden (ya da zaman aşımında kapatılmadan) sonraki başlamaz; aradaki dinlenme süresinde önceki koşunun tarayıcı
süreçleri kapanmış olmalıdır (kalmışsa beklenir, sonra sonlandırılır ve kaydedilir). Bir koşunun içinde iki tarayıcı
yalnızca yapılandırma öyle istediğinde açıktır (baseline: köprü + ayrı canlı tarayıcı).

    python scripts/push_memory_matrix.py --minutes 30 --cooldown 120
    python scripts/push_memory_matrix.py --configs "baseline;shared-browser+multi-sport;flags" --minutes 30
    python scripts/push_memory_matrix.py --dry-run
    python scripts/push_memory_matrix.py --configs flags -- --flags renderer-limit=1,disable-gpu   # proba ek argüman

Camoufox yapılandırması kendi sanal ortamının Python'uyla koşar (`--camoufox-python`, varsayılan
~/.cache/s6-engines/camoufox-venv/bin/python); yoksa prob o koşuyu "skipped" yazar. Sonuçlar:
research/push_memory/<run-id>/ (prob) ve research/push_memory/matrix-<id>.jsonl (koşu başına bir satır: komut,
çıkış kodu, süre, özetin durumu ve kararlı evrenin PSS'i).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
PROBE = os.path.join(SCRIPTS, "push_memory_probe.py")
OUT_ROOT_DEFAULT = os.path.join(ROOT, "research", "push_memory")
PROFILE_ROOT_DEFAULT = os.path.expanduser("~/.cache/sofascore_research/push_memory_probe")
CAMOUFOX_PYTHON_DEFAULT = os.path.expanduser("~/.cache/s6-engines/camoufox-venv/bin/python")

DEFAULT_MATRIX = (
    "baseline", "shared-browser", "multi-sport", "shared-browser+multi-sport", "flags", "block-third-party",
    "live-filter", "reload-every", "gc-pressure", "hidden", "engine-headless-shell", "engine-camoufox",
    "engine-lightpanda",
)
# Açılış evreleri (köprü, canlı tarayıcı, sekmeler arası) ve kapanış için kararlı evrenin üstüne eklenen pay
OVERHEAD_MINUTES = 20.0


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ")


def is_probe_browser(argv: Sequence[str], profile_root: str) -> bool:
    """
    Önceki koşudan kalmış olabilecek tarayıcı: bir argümanı prob kökünün altındaki bir yol olan süreç (Chromium
    `--user-data-dir=<yol>`, Firefox/Camoufox `-profile <yol>`, Lightpanda `--cookie-jar <yol>`). Python süreçleri
    (sürücünün ve probun kendisi, `--profile-root <kök>`) sayılmaz.
    """
    if not argv or os.path.basename(argv[0]).startswith("python"):
        return False
    root = profile_root.rstrip("/") + "/"
    return any(a.startswith(root) or a.startswith("--user-data-dir=" + root) for a in argv)


def leftover_browsers(profile_root: str, proc_root: str = "/proc") -> List[int]:
    """Önceki koşudan kalan tarayıcı süreçleri (is_probe_browser)."""
    out = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for name in names:
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            with open(os.path.join(proc_root, name, "cmdline"), "rb") as f:
                argv = [a.decode("utf-8", errors="replace") for a in f.read().split(b"\0") if a]
        except OSError:
            continue
        if is_probe_browser(argv, profile_root):
            out.append(int(name))
    return sorted(out)


def python_for(config: str, camoufox_python: str) -> str:
    if "engine-camoufox" in config.split("+") and os.path.isfile(camoufox_python):
        return camoufox_python
    return sys.executable


def command(config: str, run_id: str, args: argparse.Namespace, extra: Sequence[str]) -> List[str]:
    return [python_for(config, args.camoufox_python), PROBE, "--config", config, "--minutes", str(args.minutes),
            "--run-id", run_id, "--out-root", args.out_root, "--profile-root", args.profile_root, *extra]


def run_one(cmd: List[str], timeout: float) -> int:
    """Koşuyu bekler; zaman aşımında SIGTERM (prob tarayıcıları kapatıp özeti yazar), 90 sn sonra SIGKILL."""
    proc = subprocess.Popen(cmd, cwd=ROOT)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.send_signal(signal.SIGTERM)
        try:
            return proc.wait(timeout=90)
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.wait()


def settle(profile_root: str, wait: float, *, sleep: Callable[[float], None] = time.sleep,
           proc_root: str = "/proc") -> List[int]:
    """Önceki koşunun süreçleri kapanana kadar bekler; `wait` sonra kalanları sonlandırır ve döndürür."""
    end = time.monotonic() + wait
    while True:
        left = leftover_browsers(profile_root, proc_root)
        if not left:
            return []
        if time.monotonic() >= end:
            for pid in left:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            return left
        sleep(2.0)


def read_summary(out_root: str, run_id: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(out_root, run_id, "summary.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def run_matrix(args: argparse.Namespace, extra: Sequence[str], *, runner: Callable[[List[str], float], int] = run_one,
               sleep: Callable[[float], None] = time.sleep, proc_root: str = "/proc") -> List[Dict[str, Any]]:
    configs = [c.strip() for c in args.configs.split(";") if c.strip()] if args.configs else list(DEFAULT_MATRIX)
    matrix_id = args.matrix_id or utc_stamp()
    log_path = os.path.join(args.out_root, f"matrix-{matrix_id}.jsonl")
    rows = []
    for i, config in enumerate(configs, 1):
        run_id = f"{matrix_id}-{i:02d}-{config}"
        cmd = command(config, run_id, args, extra)
        if args.dry_run:
            print(" ".join(cmd))
            rows.append({"config": config, "run_id": run_id, "command": cmd})
            continue
        killed = settle(args.profile_root, args.settle, sleep=sleep, proc_root=proc_root)
        t0 = time.time()
        code = runner(cmd, (args.minutes + args.overhead) * 60)
        summary = read_summary(args.out_root, run_id)
        row = {"config": config, "run_id": run_id, "exit_code": code, "minutes": round((time.time() - t0) / 60, 1),
               "status": summary.get("status"), "reasons": summary.get("reasons"),
               "steady_pss_mb_median": (summary.get("steady") or {}).get("pss_mb_median"),
               "leftover_killed": killed, "python": os.path.basename(os.path.dirname(os.path.dirname(cmd[0])))}
        rows.append(row)
        os.makedirs(args.out_root, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if i < len(configs):
            sleep(args.cooldown)
    return rows


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Run push_memory_probe configurations one after another.")
    ap.add_argument("--configs", help="';'-separated configs (each may combine with +); default: the full matrix")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--cooldown", type=float, default=120.0, help="seconds between two runs (default 120)")
    ap.add_argument("--settle", type=float, default=60.0,
                    help="seconds to wait for leftover browsers of the previous run before terminating them")
    ap.add_argument("--overhead", type=float, default=OVERHEAD_MINUTES,
                    help="minutes added to --minutes for a run's timeout (launch phases, tab gaps, shutdown)")
    ap.add_argument("--out-root", default=OUT_ROOT_DEFAULT)
    ap.add_argument("--profile-root", default=PROFILE_ROOT_DEFAULT)
    ap.add_argument("--camoufox-python", default=CAMOUFOX_PYTHON_DEFAULT)
    ap.add_argument("--matrix-id")
    ap.add_argument("--dry-run", action="store_true")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: List[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, extra = argv[:cut], argv[cut + 1:]
    run_matrix(parser().parse_args(argv), extra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
