"""Contract for bottom progress dock visibility (mirrors frontend/src/lib/progressBarMode.ts)."""


def progress_bar_mode(*, is_running: bool, status: str, dismissed: bool, minimized: bool) -> str:
    terminal = status in ("Completed", "Failed", "Cancelled")
    if dismissed and not is_running:
        return "hidden"
    if not is_running and not terminal:
        return "hidden"
    if minimized:
        return "mini"
    return "full"


def test_idle_hidden():
    assert progress_bar_mode(is_running=False, status="Idle", dismissed=False, minimized=False) == "hidden"


def test_running_full():
    assert progress_bar_mode(is_running=True, status="Running", dismissed=False, minimized=False) == "full"


def test_running_mini():
    assert progress_bar_mode(is_running=True, status="Running", dismissed=False, minimized=True) == "mini"


def test_finished_stays_until_dismissed():
    assert progress_bar_mode(is_running=False, status="Cancelled", dismissed=False, minimized=False) == "full"
    assert progress_bar_mode(is_running=False, status="Completed", dismissed=True, minimized=False) == "hidden"


def test_dismissed_clears_when_running_again():
    # UI resets dismissed on new job; while running, dismissed must not hide the bar
    assert progress_bar_mode(is_running=True, status="Running", dismissed=True, minimized=False) == "full"
