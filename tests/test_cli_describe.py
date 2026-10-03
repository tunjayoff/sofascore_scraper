"""
`describe`: CLI'nin makinece okunur öz tanımı (docs/design/02-services.md bölüm 4.1; plan maddesi P18).

Çıktı elle yazılmış bir liste değildir; argparse ağacından, dilim kayıt defterinden, hata tablosundan ve ayar
modelinden üretilir. Buradaki testler her bölümü kaynağıyla karşılaştırır: kaynak değişince çıktı da değişir,
ikisi ayrışamaz.

    commands    argparse ağacı (src/cli/main.py) ve komut kaydı (src/cli/commands)
    sports      spor kayıt defteri (src/sports.py)
    slices      dilim kayıt defteri (src/sports.py)
    errors      hata tablosu (src/errors.py)
    exit-codes  çıkış kodu tablosu (src/cli/exit_codes.py)
    config      yapılandırma dosyasının JSON Schema'sı (src/config/schema.py)
    schemas     CLI zarfının şeması (src/cli/output.py)

Çıktı her zaman JSON'dur ve hiçbir zaman yerelleştirilmez.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

import pytest

from src import errors, sports
from src.cli import commands as registry
from src.cli import exit_codes, output
from src.cli import main as cli_main
from src.cli.commands import meta
from src.config import config_schema, loader
from src.config import settings as model
from src.config.schema import environment_only_keys
import test_cli_skeleton as skeleton
from test_cli_skeleton import DESIGN_ERROR_TABLE, CliRunner, validate

# `main()`i bu süreçte çalıştıran ve süreçteki izlerini geri alan fixture (tests/test_cli_skeleton.py)
cli = skeleton.cli

TOPIC_KEYS = ["sports", "slices", "commands", "schemas", "config", "errors", "exit_codes"]


@pytest.fixture
def described(cli: CliRunner) -> Dict[str, Any]:
    run = cli("describe")
    assert (run.exit_code, run.stderr) == (0, "")
    return run.data


# --- biçim ---------------------------------------------------------------------------------------


def test_describe_is_always_json_and_never_localised(cli: CliRunner, monkeypatch: pytest.MonkeyPatch):
    plain = cli("describe")
    assert plain.exit_code == 0 and plain.json["command"] == "describe"  # --json verilmeden de zarf
    assert list(plain.data) == TOPIC_KEYS
    assert cli("describe", "--json").json == plain.json
    assert cli("describe", "--lang", "tr").json == plain.json
    monkeypatch.setenv("APP_LANGUAGE", "tr")
    assert cli("describe").json == plain.json
    assert "genel seçenekler" not in plain.stdout


@pytest.mark.parametrize("topic", meta.DESCRIBE_TOPICS)
def test_one_topic_is_the_same_as_its_part_of_the_whole(cli: CliRunner, described: Dict[str, Any], topic: str):
    key = topic.replace("-", "_")
    assert cli("describe", topic).data == {key: described[key]}


def test_topics_are_the_ones_of_the_design():
    assert meta.DESCRIBE_TOPICS == ("sports", "slices", "commands", "schemas", "config", "errors", "exit-codes")
    assert [topic.replace("-", "_") for topic in meta.DESCRIBE_TOPICS] == TOPIC_KEYS


# --- commands: argparse ağacı --------------------------------------------------------------------


def _english():
    return cli_main.translator("en")[0]


def _own_actions(parser: argparse.ArgumentParser) -> List[argparse.Action]:
    return [
        action for action in parser._actions
        if not isinstance(action, (argparse._HelpAction, argparse._SubParsersAction))
        and action.dest not in cli_main.GLOBAL_DESTS
    ]


def _leaf_parsers(parser: argparse.ArgumentParser, path: tuple = ()) -> Dict[tuple, argparse.ArgumentParser]:
    """Ağacı argparse'ın kendi yapısından yürür (kayda bakmadan): alt komutu olmayan her ayrıştırıcı bir komuttur."""
    found: Dict[tuple, argparse.ArgumentParser] = {}
    subs = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)]
    if not subs:
        found[path] = parser
    for action in subs:
        for name, child in action.choices.items():
            found.update(_leaf_parsers(child, path + (name,)))
    return found


def test_commands_are_generated_from_the_argparse_tree(described: Dict[str, Any]):
    doc = described["commands"]
    tree = cli_main.build_tree(_english(), "ssc")
    leaves = _leaf_parsers(tree.root)

    assert doc["prog"] == "ssc"
    assert [command["name"] for command in doc["commands"]] == sorted(" ".join(path) for path in leaves)
    assert [command["name"] for command in doc["commands"]] == [command.name for command in registry.commands()]
    assert doc["groups"] == [{"name": group.name, "help": _english()(group.help)} for group in registry.groups()]
    assert {"name": "config", "help": "show, check or start the configuration"} in doc["groups"]

    for command in doc["commands"]:
        parser = leaves[tuple(command["name"].split())]
        own = _own_actions(parser)
        assert [option["flags"] for option in command["options"]] == [a.option_strings for a in own if a.option_strings]
        assert [argument["name"] for argument in command["arguments"]] == [a.dest for a in own if not a.option_strings]
        for option in command["options"] + command["arguments"]:
            action = next(a for a in own if a.dest == option["name"])
            assert option["help"] == action.help and option["help"]
            assert option["takes_value"] == (action.nargs != 0)
            assert option["choices"] == (list(action.choices) if action.choices is not None else None)
            assert option["required"] == bool(action.required)
        registered = registry.find(tuple(command["name"].split()))
        assert command["help"] == _english()(registered.help)
        assert (command["always_json"], command["loads_settings"]) == (registered.always_json, registered.settings)


def test_global_options_are_the_ones_of_the_design(described: Dict[str, Any]):
    options = described["commands"]["global_options"]
    flags = [flag for option in options for flag in option["flags"]]
    # docs/design/02-services.md bölüm 4.2 (--log-format, --wait ve --progress P19 ile geldi)
    assert flags == [
        "--config", "--data-dir", "--json", "--output", "--quiet", "--verbose", "--log-level", "--log-format",
        "--no-color", "--lang", "--rate", "--ignore-breaker", "--wait", "--progress", "--version",
    ]
    by_flag = {option["flags"][0]: option for option in options}
    assert by_flag["--output"]["choices"] == ["text", "json", "ndjson"]
    assert by_flag["--lang"]["choices"] == ["en", "tr"]
    assert by_flag["--log-level"]["choices"] == ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    assert by_flag["--log-format"]["choices"] == ["text", "json"]
    assert by_flag["--progress"]["choices"] == ["none", "text", "ndjson"]
    assert by_flag["--wait"]["takes_value"] is True and by_flag["--wait"]["metavar"] == "SECONDS"
    assert by_flag["--json"]["takes_value"] is False and by_flag["--config"]["takes_value"] is True
    # --json ve --output aynı değeri yazar
    assert {option["name"] for option in options} - {"version"} == set(cli_main.GLOBAL_DESTS)
    assert by_flag["--json"]["name"] == by_flag["--output"]["name"] == "output"
    # Her komutun ayrıştırıcısı genel bayrakları da kabul eder (komuttan sonra verilebilsinler)
    tree = cli_main.build_tree(_english(), "ssc")
    for path, parser in tree.parsers.items():
        dests = {action.dest for action in parser._actions}
        assert set(cli_main.GLOBAL_DESTS) <= dests, path


def test_command_options_of_this_item(described: Dict[str, Any]):
    commands = {command["name"]: command for command in described["commands"]["commands"]}
    assert [option["flags"][0] for option in commands["doctor"]["options"]] == ["--strict", "--live", "--only", "--skip"]
    assert commands["describe"]["arguments"][0]["choices"] == list(meta.DESCRIBE_TOPICS)
    assert commands["describe"]["arguments"][0]["nargs"] == "?"
    assert [option["flags"] for option in commands["config init"]["options"]] == [["--from-legacy"]]
    assert [option["flags"] for option in commands["diagnostics"]["options"]] == [["--out"]]
    assert commands["version"]["options"] == [] and commands["config show"]["options"] == []
    assert "budget" in commands["doctor"]["options"][2]["help"]  # --only: denetim adları, ek denetim dahil


# --- sports, slices: kayıt defteri ---------------------------------------------------------------


def test_sports_are_the_registry(described: Dict[str, Any]):
    assert [sport["slug"] for sport in described["sports"]] == list(sports.sport_slugs())
    for entry, spec in zip(described["sports"], sports.SPORTS, strict=True):
        assert entry == {
            "slug": spec.slug, "name": spec.name, "score_family": spec.score_family, "slices": list(spec.detail_slices),
        }
        assert entry["slices"] == [item.key for item in sports.slices_for(spec.slug)]


def test_slices_are_the_registry(described: Dict[str, Any]):
    assert [entry["key"] for entry in described["slices"]] == [item.key for item in sports.DETAIL_SLICES]
    for entry, item in zip(described["slices"], sports.DETAIL_SLICES, strict=True):
        assert entry == {
            "key": item.key,
            "owner": "event",
            "path": item.path,
            "sports": sorted(item.sports) if item.sports is not None else None,
            "default_enabled": item.default_enabled,
            "counts_for_completeness": item.required,
        }
    by_key = {entry["key"]: entry for entry in described["slices"]}
    assert by_key["point_by_point"]["sports"] == ["tennis"] and by_key["point_by_point"]["counts_for_completeness"] is False
    assert by_key["statistics"]["sports"] is None and by_key["statistics"]["path"] == "/event/{event_id}/statistics"


# --- errors, exit-codes: tablolar ----------------------------------------------------------------


def test_errors_are_the_error_table(described: Dict[str, Any]):
    assert described["errors"] == [spec.to_dict() for spec in errors.ERROR_TABLE]
    assert {
        row["code"]: (row["class"], row["exit_code"], tuple(row["http_status"])) for row in described["errors"]
    } == DESIGN_ERROR_TABLE
    by_code = {row["code"]: row for row in described["errors"]}
    assert by_code["cancelled"]["exit_codes"] == [130, 143]
    assert by_code["unauthorized"]["exit_codes"] == [] and by_code["partial"]["raised"] is False
    assert all(
        sorted(row) == ["class", "code", "exit_code", "exit_codes", "http_status", "meaning", "raised"]
        for row in described["errors"]
    )
    # Her PlatformError alt sınıfı tabloda (tests/test_cli_skeleton.py aynı şeyi sınıflardan yola çıkarak sınar)
    described_classes = {row["class"] for row in described["errors"] if row["class"]}
    assert {cls.__name__ for cls in errors.platform_error_classes()} <= described_classes


def test_exit_codes_are_the_exit_code_table(described: Dict[str, Any]):
    doc = described["exit_codes"]
    assert doc["precedence"] == [5, 4, 6, 3, 1]
    assert doc["codes"] == [spec.to_dict() for spec in exit_codes.EXIT_CODES]
    assert [row["code"] for row in doc["codes"]] == [0, 1, 2, 3, 4, 5, 6, 130, 143]
    # Hata tablosundaki her çıkış kodu burada tanımlı ve hata kodunu sayıyor
    by_code = {row["code"]: row for row in doc["codes"]}
    for row in described["errors"]:
        for code in row["exit_codes"]:
            assert row["code"] in by_code[code]["error_codes"], row


# --- config, schemas -----------------------------------------------------------------------------


def test_config_is_the_schema_of_the_settings_model(described: Dict[str, Any]):
    doc = described["config"]
    assert doc["schema"] == config_schema()
    assert doc["file_name"] == loader.CONFIG_FILE_NAME == "sofascore.toml"
    assert doc["precedence"] == list(loader.LAYERS) == ["default", "dotenv", "overrides", "file", "env", "flag"]
    assert doc["environment_only"] == environment_only_keys()
    assert doc["search_order"] == ["--config", "SOFASCORE_CONFIG", "./sofascore.toml", "<config dir>/sofascore.toml"]
    # Canlı kaynak seçenekleri ve `direct` uyarısı şemada (02-services.md 8.3)
    source = doc["schema"]["properties"]["live"]["properties"]["source"]
    assert source["enum"] == ["page", "direct", "poll"] and model.LIVE_DIRECT_WARNING in source["description"]


def test_schemas_describe_the_envelope(cli: CliRunner, described: Dict[str, Any]):
    doc = described["schemas"]
    assert doc["cli"] == {"id": "sofascore.cli/1", "envelope": output.ENVELOPE_SCHEMA}
    assert doc["config"] == {"id": "sofascore.config/1", "version": model.SCHEMA_VERSION, "describe": "config"}
    # Normalleştirilmiş kayıtların şeması (SC-1; P19 bağladı)
    from src import schema

    assert sorted(doc) == ["cli", "config", "data"]
    assert doc["data"] == json.loads(json.dumps(schema.describe())) and doc["data"]["version"] == schema.SCHEMA_VERSION
    # Şema gerçek çıktıları kabul eder: başarılı ve hatalı zarf
    envelope = doc["cli"]["envelope"]
    assert validate(cli("version", "--json").json, envelope) == []
    assert validate(cli("frobnicate", "--json").json, envelope) == []
    assert validate({"ok": True}, envelope) != []


def test_unknown_topic_is_a_usage_error(cli: CliRunner):
    run = cli("describe", "secrets")
    assert run.exit_code == 2 and run.stdout == ""
    assert "Usage error: argument TOPIC: invalid choice: 'secrets'" in run.stderr
    assert "usage: ssc describe [global options] [TOPIC]" in run.stderr
