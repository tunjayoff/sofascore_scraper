"""
Bu sürecin belleği ve altındaki tarayıcı süreçleri (S1-A3): köprünün geri dönüşüm kararı ve tanılama paketi için.

Yalnızca /proc ve cgroup okunur (Linux); başka sistemlerde hiçbir şey okunamaz ve işlevler (None, "") / 0 döndürür. c
projesinin sofascore_site/bridge.py'sinden (memory_mib, browser_processes) geri taşındı; DATA_DIR'e dokunmaz.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple


_PROC = "/proc"
_CGROUP_FS = "/sys/fs/cgroup"
_BROWSER_COMMS = ("chrome", "chromium", "headless_shell")
_MIB = 1024 * 1024


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _process_tree(root: int, proc: str = _PROC) -> List[Tuple[int, str]]:
    """`root` ve altındaki her süreç: (pid, comm). /proc yoksa (Linux dışı) boş."""
    children: Dict[int, List[int]] = {}
    comms: Dict[int, str] = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return []
    for name in names:
        if not name.isdigit():
            continue
        stat = _read(f"{proc}/{name}/stat")
        if not stat or ")" not in stat:
            continue
        # "pid (comm) durum ppid ...": comm boşluk ve parantez içerebilir
        head, _, tail = stat.rpartition(")")
        fields = tail.split()
        if len(fields) < 2 or not fields[1].lstrip("-").isdigit():
            continue
        pid = int(name)
        comms[pid] = head.partition("(")[2]
        children.setdefault(int(fields[1]), []).append(pid)
    out, todo = [], [root]
    while todo:
        pid = todo.pop()
        if pid in comms or pid == root:
            out.append((pid, comms.get(pid, "")))
            todo.extend(children.get(pid, []))
    return out


def browser_processes(root: Optional[int] = None, proc: str = _PROC) -> int:
    """Bu sürecin altındaki tarayıcı süreçleri (Chromium'un tarayıcı, renderer, GPU ve yardımcı süreçleri)."""
    tree = _process_tree(os.getpid() if root is None else root, proc)
    return sum(1 for _, comm in tree if comm.lower().startswith(_BROWSER_COMMS))


def memory_mib(root: Optional[int] = None, proc: str = _PROC, cgroup_fs: str = _CGROUP_FS) -> Tuple[Optional[float], str]:
    """
    (MiB, kaynağı): süreç bir systemd `.service`'inde çalışıyorsa birimin anonim belleği ve takası (cgroup'unun
    `memory.stat` anon + `memory.swap.current`; sürecin kendi birimi olduğu varsayılır); yoksa bu sürecin ve altındaki
    her sürecin orantılı bellek boyutu (`smaps_rollup` Pss; okunamazsa yerleşik boyut). `memory.current` değil: sayfa
    önbelleğini de sayar (c'nin 5. koşusunun 2,4 GB'lık tepesinin yaklaşık 600 MiB'ı), büyüyen anonim bellekti (2 saatte
    637'den 1254 MiB'a). Hiçbir şey okunamazsa (None, "") — Linux dışında da.
    """
    pid = os.getpid() if root is None else root
    own = _read(f"{proc}/{pid}/cgroup") or ""
    path = next((line[3:] for line in own.splitlines() if line.startswith("0::")), "")
    if path.endswith(".service"):
        stat = _read(f"{cgroup_fs}{path}/memory.stat") or ""
        anon = next((int(v) for k, _, v in (ln.partition(" ") for ln in stat.splitlines()) if k == "anon"), None)
        if anon is not None:
            swap = (_read(f"{cgroup_fs}{path}/memory.swap.current") or "0").strip()
            return (anon + (int(swap) if swap.isdigit() else 0)) / _MIB, "cgroup"
    total_kib, seen = 0, False
    for p, _ in _process_tree(pid, proc):
        rollup = _read(f"{proc}/{p}/smaps_rollup") or ""
        pss = next((ln.split()[1] for ln in rollup.splitlines() if ln.startswith("Pss:")), None)
        if pss is None:
            statm = (_read(f"{proc}/{p}/statm") or "").split()
            if len(statm) < 2:
                continue
            total_kib += int(statm[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)
        else:
            total_kib += int(pss)
        seen = True
    return (total_kib / 1024, "pss") if seen else (None, "")
