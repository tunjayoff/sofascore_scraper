"""
Köprünün profil kilidi (FX-23, bulgular F34 ve F36): `ssc serve` profili tutarken ikinci bir süreç kardeş geçici
profille tarayıcı açar. Gerçek tarayıcı başlatılmaz (`_launch` yamalanır); SofaScore'a istek gitmez.
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import List, Optional
from unittest.mock import AsyncMock

import pytest

from sofascore_scraper.client import bridge as cs
from sofascore_scraper.client import profile_lock

posix_only = pytest.mark.skipif(os.name == "nt", reason="Chromium's SingletonLock is a symlink on POSIX")


def _dead_pid() -> int:
    """Bitmiş bir sürecin pid'i (yeniden kullanılmış olması pek olası değil)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _lock(profile: Path, target: str) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    os.symlink(target, profile / profile_lock.POSIX_LOCK)


@posix_only
def test_owner_of_a_profile_held_by_a_live_process(tmp_path: Path) -> None:
    profile = tmp_path / "chrome_profile"
    assert profile_lock.profile_owner(str(profile)) is None
    _lock(profile, f"{socket.gethostname()}-{os.getppid()}")

    owner = profile_lock.profile_owner(str(profile))

    assert owner == profile_lock.ProfileOwner(pid=os.getppid(), host=socket.gethostname())
    assert owner.describe() == f"another process (pid {os.getppid()})"


@posix_only
def test_a_stale_lock_of_this_machine_is_free_and_a_lock_of_another_machine_is_not(tmp_path: Path) -> None:
    stale = tmp_path / "stale"
    _lock(stale, f"{socket.gethostname()}-{_dead_pid()}")
    assert profile_lock.profile_owner(str(stale)) is None
    foreign = tmp_path / "foreign"
    _lock(foreign, "some-other-host-4242")
    owner = profile_lock.profile_owner(str(foreign))
    assert owner is not None and owner.pid == 4242 and "on some-other-host" in owner.describe()
    odd = tmp_path / "odd"
    _lock(odd, "not a lock target")
    assert profile_lock.profile_owner(str(odd)) is None


def test_the_secondary_profile_is_a_private_sibling_and_stale_siblings_are_swept(tmp_path: Path) -> None:
    profile = tmp_path / "chrome_profile"
    profile.mkdir()
    dead = tmp_path / f"chrome_profile-{_dead_pid()}"
    dead.mkdir()
    (dead / "Cookies").write_text("x")
    live = tmp_path / "chrome_profile-live"  # canlı kaynakların profili: süpürülmez
    live.mkdir()

    path = profile_lock.secondary_profile(str(profile))

    assert path == f"{profile}-{os.getpid()}" and os.path.isdir(path)
    if os.name == "posix":
        assert os.stat(path).st_mode & 0o777 == 0o700
    assert not dead.exists() and live.exists() and profile.exists()
    profile_lock.remove_secondary(path)
    assert not os.path.exists(path)


@pytest.mark.parametrize(("text", "expected"), [
    ("BrowserType.launch_persistent_context: Failed to create a ProcessSingleton for your profile directory", True),
    ("[pid=1][err] Failed to create /x/SingletonLock: File exists (17)", True),
    ("Executable doesn't exist at /chromium", False),
])
def test_lock_errors_are_recognised(text: str, expected: bool) -> None:
    assert profile_lock.is_lock_error(RuntimeError(text)) is expected


def _recording_launch(bridge: cs.BrowserBridge, errors: List[Optional[Exception]]) -> List[str]:
    """`_launch` yerine: her çağrıda kullanılacak profil dizinini kaydeder, sıradaki hatayı verir."""
    used: List[str] = []

    async def launch() -> None:
        used.append(bridge._launch_dir or bridge.profile_dir)
        error = errors.pop(0) if errors else None
        if error is not None:
            raise error
        bridge.page = AsyncMock()
        bridge.page.is_closed = lambda: False

    bridge._launch = launch  # type: ignore[method-assign]
    return used


@posix_only
def test_a_profile_held_by_another_process_launches_in_a_temporary_profile(tmp_path: Path) -> None:
    profile = tmp_path / "chrome_profile"
    _lock(profile, f"{socket.gethostname()}-{os.getppid()}")
    bridge = cs.BrowserBridge(profile_dir=str(profile))
    used = _recording_launch(bridge, [])

    asyncio.run(bridge.ensure_ready())

    assert used == [f"{profile}-{os.getpid()}"] and os.path.isdir(used[0])
    asyncio.run(bridge.close())
    assert not os.path.exists(used[0]) and profile.exists()


def test_a_lock_error_at_launch_retries_once_in_a_temporary_profile(tmp_path: Path) -> None:
    profile = tmp_path / "chrome_profile"
    bridge = cs.BrowserBridge(profile_dir=str(profile))
    used = _recording_launch(bridge, [RuntimeError("Failed to create a ProcessSingleton for your profile")])

    asyncio.run(bridge.ensure_ready())

    assert used == [str(profile), f"{profile}-{os.getpid()}"]
    asyncio.run(bridge.close())
    assert not os.path.exists(used[1])


def test_another_launch_error_is_not_retried(tmp_path: Path) -> None:
    bridge = cs.BrowserBridge(profile_dir=str(tmp_path / "chrome_profile"))
    used = _recording_launch(bridge, [RuntimeError("chromium missing")])

    with pytest.raises(RuntimeError, match="chromium missing"):
        asyncio.run(bridge.ensure_ready())
    assert used == [str(tmp_path / "chrome_profile")]
    with pytest.raises(RuntimeError, match="ssc doctor"):  # yeniden deneme beklenirken: İngilizce, `ssc doctor`
        asyncio.run(bridge.ensure_ready())


@posix_only
def test_without_a_temporary_profile_the_error_names_the_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = tmp_path / "chrome_profile"
    _lock(profile, f"{socket.gethostname()}-{os.getppid()}")
    bridge = cs.BrowserBridge(profile_dir=str(profile))
    used = _recording_launch(bridge, [])

    def refuse(path: str) -> str:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(profile_lock, "secondary_profile", refuse)
    with pytest.raises(RuntimeError, match=rf"in use by another process \(pid {os.getppid()}\)"):
        asyncio.run(bridge.ensure_ready())
    assert used == []
