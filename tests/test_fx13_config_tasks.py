"""
`ssc config validate` ve `describe config` zamanlayıcının görevlerini bilir (plan maddesi FX-13; P29'un notu): bir
`[[schedule.task]]`in bilinmeyen `run` adı, seçeneği ya da bozuk cron ifadesi `serve` başlamadan, `config validate`
ile de `config_invalid`dir (çıkış kodu 2); `describe config` geçerli `run` adlarını seçenekleriyle listeler.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import test_cli_skeleton as skeleton
from sofascore_scraper.jobs.scheduler import TASK_RUNS
from test_cli_skeleton import CliRunner, write_config

cli = skeleton.cli


@pytest.mark.parametrize("task, message", [
    ('run = "download"\nevery = "1h"\n', "'download' is not a task the scheduler knows"),
    ('run = "sync"\nevery = "1h"\nscope = "all"\n', "unknown option(s) for run = 'sync': scope"),
    ('run = "backup"\ncron = "61 3 * * *"\n', "[[schedule.task]] #2 cron"),
])
def test_config_validate_refuses_a_task_serve_would_refuse(cli: CliRunner, tmp_path: Path, task: str,
                                                           message: str) -> None:
    config = write_config(tmp_path / "sofascore.toml",
                          'schema = 1\n[[schedule.task]]\nrun = "sync"\nevery = "6h"\n[[schedule.task]]\n' + task)
    run = cli("config", "validate", "--json", "--config", config)
    assert run.exit_code == 2, run.stdout
    assert run.error["code"] == "config_invalid" and message in run.error["message"]


def test_config_validate_counts_valid_tasks(cli: CliRunner, tmp_path: Path) -> None:
    config = write_config(tmp_path / "sofascore.toml", 'schema = 1\n[[schedule.task]]\nrun = "sync"\nevery = "6h"\n'
                                                       '[[schedule.task]]\nrun = "backup"\ncron = "0 3 * * *"\n')
    run = cli("config", "validate", "--json", "--config", config)
    assert run.exit_code == 0 and run.data["schedule_tasks"] == 2


def test_describe_config_lists_the_task_runs(cli: CliRunner) -> None:
    doc = cli("describe", "config", "--json").data["config"]
    assert doc["schedule_runs"] == [{"run": name, "options": sorted(run.options)} for name, run in TASK_RUNS.items()]
    assert [item["run"] for item in doc["schedule_runs"]] == ["sync", "fetch", "refresh", "backup", "prune-history"]
