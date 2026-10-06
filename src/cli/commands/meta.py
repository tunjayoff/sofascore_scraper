"""
Bilgi ve kurulum komutları (docs/design/02-services.md bölüm 4.1): veri indirmeyen, SofaScore'a bağlanmayan
komutlar.

    version                        sürüm ve şema sürümleri
    doctor                         ortam denetimi (src/doctor.py)
    describe [konu]                makinece okunur öz tanım: sporlar, dilimler, komutlar, şemalar, yapılandırma,
                                   hata tablosu, çıkış kodları; her zaman JSON
    config show|validate|init|path geçerli yapılandırma ve her değerin kaynağı; dosyanın denetimi; başlangıç
                                   dosyası; dosyanın yolu
    diagnostics                    tanılama paketi (src/diagnostics.py)

Yalnızca `doctor --live` gerçek bir istek atar ve yalnızca açıkça istenirse.

Ağır içe aktarmalar (ayar yükleyici, log, tanılama) işlevlerin içindedir: modül yüklenirken yalnızca standart
kütüphane ve saf modüller gerekir, böylece `version` ve `doctor` paketler kurulmadan da çalışır.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src import sports
from src.cli import PROG, VERSION_TEXT
from src.cli.commands import (
    CliWarning,
    CommandResult,
    Invocation,
    activate_settings,
    command,
    group,
    logs_to_stderr,
    read_settings,
)
from src.cli.exit_codes import EXIT_CODES, PRECEDENCE
from src.cli.output import ENVELOPE_SCHEMA, SCHEMA, Translator
from src.errors import ERROR_TABLE, ConfigError, StorageError, UsageError
from src.version import __version__

APP_NAME = "sofascore-scraper"


# === version =========================================================================================


def _config_schema_version() -> Optional[int]:
    """Yapılandırma dosyasının şema sürümü; ayar paketi yüklenemiyorsa (paketler kurulmamış) None."""
    try:
        from src.config import settings as model

        return int(model.SCHEMA_VERSION)
    except Exception:
        return None


def _data_schema_version() -> Optional[int]:
    """Normalleştirilmiş kayıt şemasının sürümü (src/schema); paket yüklenemiyorsa None."""
    try:
        from src.schema import models

        return int(models.SCHEMA_VERSION)
    except Exception:
        return None


def store_versions() -> Optional[Dict[str, int]]:
    """
    Bu kodun yazdığı ve okuduğu depo sürümleri: düzen (v3 ağacı), catalog.db ve state.db şemaları. Store
    yüklenemiyorsa None.
    """
    try:
        from src.store import CATALOG_SCHEMA, LAYOUT_VERSION, load_migrations

        migrations = load_migrations()
        return {
            "layout": int(LAYOUT_VERSION),
            "catalog": int(CATALOG_SCHEMA),
            "state": int(migrations[-1].version) if migrations else 0,
        }
    except Exception:
        return None


def _known(value: Any) -> Any:
    return value if value is not None else "?"


@command("version", help="ssc_help_cmd_version")
def version(inv: Invocation) -> CommandResult:
    config_schema = _config_schema_version()
    data_schema = _data_schema_version()
    store = store_versions()
    data = {
        "name": APP_NAME,
        "version": __version__,
        "python": platform.python_version(),
        "schemas": {"cli": SCHEMA, "config": config_schema, "data": data_schema},
        "store": store,
    }
    lines = [
        VERSION_TEXT,
        inv.t("ssc_version_schemas", cli=SCHEMA, config=_known(config_schema)),
        inv.t("ssc_version_data_schema", data=_known(data_schema)),
    ]
    if store is not None:
        lines.append(inv.t("ssc_version_store", layout=store["layout"], catalog=store["catalog"],
                           state=store["state"]))
    return CommandResult(data=data, text="\n".join(lines))


# === doctor ==========================================================================================


def _doctor_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    from src import doctor

    ids = ", ".join(doctor.CHECK_IDS + doctor.EXTRA_CHECK_IDS)
    parser.add_argument("--strict", action="store_true", help=t("doctor_cli_strict"))
    parser.add_argument("--live", action="store_true", help=t("doctor_cli_live"))
    parser.add_argument("--only", metavar="IDS", help=t("doctor_cli_only", ids=ids))
    parser.add_argument("--skip", metavar="IDS", help=t("doctor_cli_skip"))


def _ids(raw: Optional[str]) -> Optional[List[str]]:
    return [part.strip() for part in raw.split(",") if part.strip()] if raw else None


def _effective_rate(inv: Invocation) -> Tuple[Optional[Tuple[float, str]], List[CliWarning]]:
    """
    İstek bütçesinin geçerli değeri ve kaynağı: yapılandırma dosyası, `.env`, ortam ve `--rate` birlikte.
    Ayar paketi yüklenemiyorsa (paketler kurulmamış) ya da yapılandırma geçersizse değer None'dır: denetim o
    zaman yalnızca ortamı ve `.env`'i okur. Geçersiz yapılandırma bir uyarı olarak döner: denetimler ortamı
    okur, yapılandırma dosyasını denetlemez (`config validate` denetler).
    """
    try:
        loaded = read_settings(inv)
    except ConfigError as e:
        return None, [CliWarning("config_invalid", str(e))]
    except Exception:
        return None, []
    source = loaded.source("client.rate")
    return (float(loaded.settings.client.rate), source.name or source.layer), []


@command("doctor", help="ssc_help_cmd_doctor", description="doctor_cli_description", configure=_doctor_arguments)
def doctor(inv: Invocation) -> CommandResult:
    from src import doctor as checks

    only, skip = _ids(inv.args.only), _ids(inv.args.skip)
    valid = checks.CHECK_IDS + checks.EXTRA_CHECK_IDS + ("live",)
    unknown = [check_id for check_id in (only or []) + (skip or []) if check_id not in valid]
    if unknown:
        raise UsageError(
            f"unknown check id: {', '.join(unknown)} (valid: {', '.join(valid)})",
            {"unknown": unknown, "valid": list(valid)},
        )

    if inv.args.live:
        # Canlı istek uygulamanın ayarlarıyla atılır (proxy, profil, istek bütçesi): ayarlar yüklenir, loglar stderr'e
        activate_settings(inv)

    environ = dict(os.environ)
    data_dir = inv.flags.get("storage.data_dir")
    if data_dir:
        environ["DATA_DIR"] = str(data_dir)
    ctx = checks.Context(environ=environ, lang=inv.lang)
    rate, warnings = _effective_rate(inv)
    if rate is not None:
        ctx.request_rate, ctx.request_rate_source = rate

    # Denetimler sırasında stdout'a yazılan her şey stderr'e gider: stdout'ta yalnızca rapor kalır
    with contextlib.redirect_stdout(sys.stderr):
        results = checks.run_checks(ctx, only=only, skip=skip, live=inv.args.live, extra=True)
    return CommandResult(
        data=checks.report(results, ctx),
        text=checks.render_text(results, ctx),
        exit_code=checks.exit_code(results, strict=inv.args.strict),
        warnings=warnings,
    )


# === describe ========================================================================================

DESCRIBE_TOPICS = ("sports", "slices", "commands", "schemas", "config", "errors", "exit-codes")


def _describe_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument(
        "topic", nargs="?", choices=DESCRIBE_TOPICS, metavar="TOPIC",
        help=t("ssc_help_describe_topic", topics=", ".join(DESCRIBE_TOPICS)),
    )


def describe_sports() -> List[Dict[str, Any]]:
    return [
        {"slug": spec.slug, "name": spec.name, "score_family": spec.score_family, "slices": list(spec.detail_slices)}
        for spec in sports.SPORTS
    ]


def describe_slices() -> List[Dict[str, Any]]:
    """
    Dilim kayıt defteri (src/sports.py): maç dilimleri, bahis oranları ve maç dışı dilimler (plan maddesi P28;
    `owner`). `not_in`: dilimin istenmediği sporlar; `optional_in`: istendiği ama tamlık hesabına girmediği
    sporlar. `selected_in`: etkin yapılandırmanın varsayılan seçiminin ([defaults] slices ve [slices.<spor>]; plan
    maddesi P27) dilimi seçtiği ve dilimin istendiği sporlar; bir takip kendi seçimini verebilir.
    """
    from src.services import planning

    policy = planning.configured_policy()
    chosen = {sport: {spec.key for spec in sports.chosen_slices(policy.for_sport(sport)) if spec.applies_to(sport)}
              for sport in sports.sport_slugs()}
    selected_in = {item.key: [sport for sport, keys in chosen.items() if item.key in keys]
                   for item in sports.registered_slices()}
    return [
        {
            "key": item.key,
            "owner": item.owner,
            "path": item.path,
            "sports": sorted(item.sports) if item.sports is not None else None,
            "not_in": sorted(item.not_in),
            "default_enabled": item.default_enabled,
            "counts_for_completeness": item.required,
            "optional_in": sorted(item.optional_in),
            "group": item.group,
            "phases": [phase for phase in sports.PHASES if phase in item.phases],
            "keep_history": item.keep_history,
            "max_age_seconds": int(item.max_age.total_seconds()) if item.max_age is not None else None,
            "selected_in": selected_in[item.key],
        }
        for item in sports.registered_slices()
    ]


def describe_schemas() -> Dict[str, Any]:
    from src.config import schema as config_schema
    from src.config import settings as model

    from src import schema as data_schema

    return {
        "cli": {"id": SCHEMA, "envelope": ENVELOPE_SCHEMA},
        "config": {"id": config_schema.SCHEMA_ID, "version": model.SCHEMA_VERSION, "describe": "config"},
        "data": data_schema.describe(),
    }


def describe_config() -> Dict[str, Any]:
    from src.config import loader
    from src.config import schema as config_schema

    return {
        "file_name": loader.CONFIG_FILE_NAME,
        "search_order": ["--config", loader.CONFIG_ENV, f"./{loader.CONFIG_FILE_NAME}", f"<config dir>/{loader.CONFIG_FILE_NAME}"],
        "precedence": list(loader.LAYERS),
        "environment_only": config_schema.environment_only_keys(),
        "schema": config_schema.config_schema(),
        "live_sources": describe_live_sources(),
        "schedule_runs": describe_schedule_runs(),
    }


def describe_schedule_runs() -> List[Dict[str, Any]]:
    """
    `[[schedule.task]]`in `run` adları ve her birinin seçenekleri (src/jobs/scheduler.py TASK_RUNS; plan maddesi
    FX-13): `config validate` aynı tabloyla denetler, `serve` başlarken de.
    """
    from src.jobs.scheduler import TASK_RUNS

    return [{"run": name, "options": sorted(run.options)} for name, run in TASK_RUNS.items()]


# Canlı kaynaklar (02-services.md 8.2 ve 8.3); `ssc watch --source` ve `[live] source`: page (P24), poll ve açık
# seçimle direct (P31; uyarısıyla birlikte listelenir). Liste src/services/live/supervisor.py
# AVAILABLE_SOURCES ile aynıdır (bu modül servisi içe aktarmaz: describe hafif kalır).
_LIVE_SOURCE_TEXT = {
    "page": "listens to the push connection that SofaScore's own page opens; handles no credential",
    "direct": "connects a lightweight client to the push server itself, with the site's own client credential "
              "read at runtime; an explicit opt-in, never chosen for you",
    "poll": "requests the live list per sport every poll interval, and the pages of dropped, near-end and stuck "
            "events; the fallback of every source",
}


def describe_live_sources() -> List[Dict[str, Any]]:
    from src.config import settings as model

    default = model.LiveSettings().source
    available = ("page", "direct", "poll")
    return [
        {
            "name": name,
            "default": name == default,
            "available": name in available,
            "opt_in": name == "direct",
            "description": _LIVE_SOURCE_TEXT[name],
            "warning": model.LIVE_DIRECT_WARNING if name == "direct" else None,
        }
        for name in model.LIVE_SOURCES
    ]


def describe_errors() -> List[Dict[str, Any]]:
    return [spec.to_dict() for spec in ERROR_TABLE]


def describe_exit_codes() -> Dict[str, Any]:
    return {"codes": [spec.to_dict() for spec in EXIT_CODES], "precedence": list(PRECEDENCE)}


@command("describe", help="ssc_help_cmd_describe", configure=_describe_arguments, always_json=True)
def describe(inv: Invocation) -> CommandResult:
    def commands() -> Dict[str, Any]:
        # `describe` çıktısı yerelleştirilmez: komut ve seçenek açıklamaları her zaman İngilizcedir
        assert inv.describe_commands is not None and inv.translator is not None
        english, _ = inv.translator("en")
        return inv.describe_commands(english)

    builders = {
        "sports": describe_sports,
        "slices": describe_slices,
        "commands": commands,
        "schemas": describe_schemas,
        "config": describe_config,
        "errors": describe_errors,
        "exit-codes": describe_exit_codes,
    }
    wanted: Iterable[str] = (inv.args.topic,) if inv.args.topic else DESCRIBE_TOPICS
    return CommandResult(data={topic.replace("-", "_"): builders[topic]() for topic in wanted})


# === config ==========================================================================================

group("config", help="ssc_help_cmd_config")


def _load_warnings(loaded: Any, *, logged: bool) -> List[CliWarning]:
    """Yükleyicinin uyarıları ve bugünkü adla verilip okunamamış (varsayılana düşmüş) değerler."""
    from src.config import loader

    warnings = [CliWarning(warning.code, warning.message, logged=logged) for warning in loaded.warnings]
    for key, raw in sorted(loaded.invalid_legacy.items()):
        variable = loader.LEGACY_BY_KEY[key].env if key in loader.LEGACY_BY_KEY else key
        warnings.append(CliWarning(
            "legacy_value_ignored",
            f"{variable}={raw!r} is not valid and is ignored; {key} is {loaded.settings.get(key)!r}.",
        ))
    return warnings


def _value_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


@command("config show", help="ssc_help_cmd_config_show", settings=True)
def config_show(inv: Invocation) -> CommandResult:
    from src.config import loader

    loaded = loader.active()
    rows = loaded.describe()
    data = {"config_file": loaded.config_file, "overrides_file": loaded.overrides_file, "values": rows}

    lines = [
        inv.t("ssc_config_file", path=loaded.config_file) if loaded.config_file else inv.t("ssc_config_no_file"),
    ]
    if loaded.overrides_file:
        lines.append(inv.t("ssc_config_overrides_file", path=loaded.overrides_file))
    lines.append("")
    width = max(len(row["key"]) for row in rows)
    for row in rows:
        origin = row["source"] + (f": {row['from']}" if row["from"] and row["from"] != row["key"] else "")
        lines.append(f"{row['key']:<{width}} = {_value_text(row['value'])}  [{origin}]")
    # Yükleyicinin uyarıları etkinleştirmede log satırı olarak yazıldı; okunamayan eski değerler burada eklenir
    extra = [warning for warning in _load_warnings(loaded, logged=True) if warning.code == "legacy_value_ignored"]
    return CommandResult(data=data, text="\n".join(lines), warnings=extra)


def _check_sinks(loaded: Any) -> None:
    """
    Sink'leri açmadan kurar (`sinks.build_sinks`). İmza anahtarı ortamdan ya da `.env`'den okunur; süreç
    ortamına dokunulmaz. Kurulamayan sink ConfigError'dır (`config_invalid`).
    """
    import dotenv

    from src import sinks
    from src.paths import env_file_path

    try:
        file_values = {key: value for key, value in dotenv.dotenv_values(env_file_path()).items() if value is not None}
    except Exception:
        file_values = {}
    try:
        sinks.build_sinks(loaded.settings.sinks, environ={**file_values, **os.environ},
                          data_dir=os.path.abspath(loaded.settings.storage.data_dir))
    except ValueError as e:  # sink sınıfının kendi denetimi (ör. adresin biçimi)
        raise ConfigError(f"[[sink]]: {e}") from None


@command("config validate", help="ssc_help_cmd_config_validate")
def config_validate(inv: Invocation) -> CommandResult:
    """
    Dosyayı ve ortamı sürece dokunmadan denetler. Geçersizse ConfigError: `config_invalid`, çıkış kodu 2.
    Sink'ler de kurulur (açılmadan): bilinmeyen bir seçenek, eksik imza anahtarı ya da veri dizininin içine
    yazan dosya sink'i burada bildirilir, komut çalışırken değil. Zamanlayıcının görevleri de `serve`in
    denetimiyle denetlenir (`run` adı, seçenekleri, cron ifadesi; plan maddesi FX-13).
    """
    loaded = read_settings(inv)
    if loaded.settings.sinks:
        _check_sinks(loaded)
    if loaded.settings.schedule.tasks:
        # `serve`in başlarken yaptığı denetim (run adı, seçenekleri, cron ifadesi; plan maddesi FX-13)
        from src.jobs.scheduler import check_tasks

        check_tasks(loaded.settings.schedule.tasks)
    data = {
        "valid": True,
        "config_file": loaded.config_file,
        "overrides_file": loaded.overrides_file,
        "follows": len(loaded.settings.follows),
        "sinks": len(loaded.settings.sinks),
        "schedule_tasks": len(loaded.settings.schedule.tasks),
    }
    text = inv.t("ssc_config_valid", path=loaded.config_file) if loaded.config_file else inv.t("ssc_config_valid_no_file")
    return CommandResult(data=data, text=text, warnings=_load_warnings(loaded, logged=False))


@command("config path", help="ssc_help_cmd_config_path")
def config_path(inv: Invocation) -> CommandResult:
    """Kullanılan yapılandırma dosyasının yolu. Dosyanın içeriği okunmaz: bozuk bir dosyanın yolu da bulunur."""
    from src.config import loader

    found = loader.find_config_file(inv.config_file)
    disabled = not inv.config_file and (os.environ.get(loader.CONFIG_ENV) or "").strip().lower() == loader.CONFIG_DISABLED
    searched = [
        os.path.abspath(loader.CONFIG_FILE_NAME),
        os.path.abspath(os.path.join(os.environ.get("SOFASCORE_CONFIG_DIR") or "config", loader.CONFIG_FILE_NAME)),
    ]
    data = {"config_file": str(found) if found is not None else None, "search_disabled": disabled, "searched": searched}
    if found is not None:
        return CommandResult(data=data, text=str(found))
    note = inv.t("ssc_config_path_disabled") if disabled else inv.t("ssc_config_path_none", places=", ".join(searched))
    return CommandResult(data=data, notes=[note])


# --- config init: TOML metni ------------------------------------------------------------------------


def toml_value(value: Any) -> str:
    """Bir ayar değerinin TOML yazımı: dizge, mantıksal, sayı ya da bunların listesi."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        # JSON dizgesi geçerli bir TOML temel dizgesidir; DEL (0x7f) TOML'de ham yazılamaz
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise TypeError(f"no TOML form for {type(value).__name__}")


_TEMPLATE_HEADER = """\
# sofascore.toml: configuration of SofaScore Scraper {version}.
#
# Every value below is the built-in default and is commented out; remove the "# " in front of a line to
# set it. The file is read from, in this order: --config, the SOFASCORE_CONFIG variable, sofascore.toml in
# the project folder, sofascore.toml in the config folder.
# Precedence, weakest to strongest: built-in defaults, .env, overrides.json, this file, environment
# variables (SOFASCORE_<SECTION>__<KEY>, e.g. SOFASCORE_CLIENT__RATE=5), command-line flags.
# Relative paths in this file are resolved against the folder that holds it.
#
#   {prog} config validate   checks the file
#   {prog} config show       prints every value and where it comes from
#   {prog} describe config   prints the JSON Schema of this file
"""

_LEGACY_HEADER = """\
# sofascore.toml: written by `{prog} config init --from-legacy` (SofaScore Scraper {version}).
#
# The equivalent of today's settings: the values that come from .env and from the environment under their
# current names, and one [[follow]] per league of {leagues}.
# Once this file is in place the league list is read from here and no longer from leagues.txt.
# Paths are written as absolute paths, so the file can be saved in any of the places it is searched in.
#
#   {prog} config validate   checks the file
#   {prog} config show       prints every value and where it comes from
"""


def _comment(text: str) -> List[str]:
    return [f"# {line}".rstrip() for line in text.splitlines()]


def default_config_text() -> str:
    """Her anahtarı varsayılanıyla ve açıklamasıyla, yorum satırı olarak taşıyan başlangıç dosyası."""
    from src.config import settings as model

    first_sport = sports.sport_slugs()[0]
    lines = _TEMPLATE_HEADER.format(version=__version__, prog=PROG).splitlines()
    lines += ["", f"schema = {model.SCHEMA_VERSION}"]
    for section, keys in model.SETTING_KEYS.items():
        lines += ["", f"[{section}]"]
        for name, f in keys.items():
            if not f.metadata["in_file"]:
                continue
            lines += _comment(f.metadata["doc"])
            lines.append(f"# {name} = {toml_value(model.default_of(f))}")
    lines += [
        "",
        "# Scheduled tasks; exactly one of every, cron per task.",
        "# [[schedule.task]]",
        "# run = \"sync\"",
        "# every = \"6h\"",
        "",
        "# Per-sport changes to the default slice selection.",
        f"# [slices.{first_sport}]",
        "# enable = []",
        "# disable = []",
        "",
        "# What to keep up to date: one [[follow]] per tournament, team, player or event.",
        "# [[follow]]",
        "# name = \"premier-league\"",
        f"# sport = \"{first_sport}\"",
        "# tournament = 17",
        "# seasons = \"current\"        # current | all | last:N | [season ids]",
        "",
        "# Where events are delivered: stdout, file or webhook.",
        "# [[sink]]",
        "# name = \"feed\"",
        "# type = \"file\"",
        "# path = \"out/events.ndjson\"",
        "# events = [\"*\"]",
    ]
    return "\n".join(lines) + "\n"


def _absolute(value: str) -> str:
    """Bugünkü adlarla verilen göreli yol çalışma dizinine (proje kökü) göredir; dosyada mutlak yazılır."""
    if not value or value.startswith("~") or os.path.isabs(value):
        return value
    return os.path.abspath(value)


def legacy_config_text(loaded: Any, leagues: Mapping[int, str], league_sports: Mapping[int, str],
                       leagues_file: str) -> str:
    """
    Bugünkü kaynakların (`.env`, bugünkü adlarla ortam, leagues.txt, league_sports.json) TOML karşılığı.

    Yalnızca bugünkü adla verilmiş değerler yazılır. Gizli değerler dosyaya yazılmaz: proxy adresi için onu
    taşıyan değişkenin adı (`proxy_env`) yazılır. Dosyanın kabul etmediği bir değer (ör. boş API adresi)
    yorum satırı olarak bırakılır ve bugünkü kaynağından okunmaya devam eder.
    """
    from src.config import loader
    from src.config import settings as model

    lines = _LEGACY_HEADER.format(version=__version__, prog=PROG, leagues=leagues_file).splitlines()
    lines += ["", f"schema = {model.SCHEMA_VERSION}"]
    for section, keys in model.SETTING_KEYS.items():
        body: List[str] = []
        for name, f in keys.items():
            key = f"{section}.{name}"
            source = loaded.source(key)
            if not source.legacy or not f.metadata["in_file"]:
                continue
            value = loaded.settings.get(key)
            if f.metadata["secret"]:
                # Gizli değer dosyaya girmez; kardeş `<ad>_env` anahtarı varsa değişkenin adı yazılır
                if value and f"{name}_env" in keys:
                    body.append(f"{name}_env = {toml_value(source.name)}")
                continue
            if f.metadata["kind"] == model.KIND_PATH:
                value = _absolute(value)
            try:
                loader.coerce(f, list(value) if isinstance(value, tuple) else value)
            except ValueError:
                body.append(f"# {name}: the value of {source.name} cannot be written here; it is still read from {source.name}")
                continue
            body.append(f"{name} = {toml_value(value)}  # {source.name}")
        if section == "client" and any(line.startswith("proxy_env = ") for line in body) \
                and not any(line.startswith("use_proxy = ") for line in body):
            # Dosyada verilen bir proxy kendiliğinden kullanılır; bugün ise yalnızca USE_PROXY=true ile
            body.append("use_proxy = false  # USE_PROXY is not set")
        if body:
            lines += ["", f"[{section}]", *body]

    used: Dict[str, int] = {}
    for league_id, name in leagues.items():
        used[name] = used.get(name, 0) + 1
    for league_id, name in leagues.items():
        label = name if used[name] == 1 else f"{name} ({league_id})"
        lines += ["", "[[follow]]", f"name = {toml_value(label)}"]
        sport = league_sports.get(league_id)
        if sport:
            lines.append(f"sport = {toml_value(sport)}")
        else:
            lines.append(f"# sport is not known for this league; one of: {', '.join(sports.sport_slugs())}")
        lines += [f"tournament = {int(league_id)}", "seasons = \"all\""]
    return "\n".join(lines) + "\n"


def _config_init_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--from-legacy", dest="from_legacy", action="store_true", help=t("ssc_help_config_init_from_legacy"))


@command("config init", help="ssc_help_cmd_config_init", configure=_config_init_arguments)
def config_init(inv: Invocation) -> CommandResult:
    """
    Bir yapılandırma dosyasının metnini stdout'a yazar; dosyayı kendisi oluşturmaz (uygulama yapılandırma
    dosyasını hiçbir zaman yazmaz: 02-services.md 4.3).
    """
    if not inv.args.from_legacy:
        text = default_config_text()
        leagues_count: Optional[int] = None
    else:
        logs_to_stderr()
        from src.config_manager import ConfigManager
        from src.web import league_sports

        # Yapılandırma dosyası hesaba katılmaz: yalnızca bugünkü kaynaklar okunur
        loaded = read_settings(inv, config_file=False)
        manager = ConfigManager()
        leagues = manager.get_leagues()
        leagues_file = os.path.abspath(manager.league_config_path)
        text = legacy_config_text(loaded, leagues, league_sports.load(manager.league_config_path), leagues_file)
        leagues_count = len(leagues)
    data = {"from_legacy": bool(inv.args.from_legacy), "follows": leagues_count, "toml": text}
    typed = f"{inv.out.prog} config init" + (" --from-legacy" if inv.args.from_legacy else "")
    return CommandResult(data=data, text=text.rstrip("\n"), notes=[inv.t("ssc_config_init_note", command=typed)])


# === diagnostics =====================================================================================


def _diagnostics_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--out", metavar="PATH", help=t("ssc_help_diagnostics_out"))


@command("diagnostics", help="ssc_help_cmd_diagnostics", configure=_diagnostics_arguments, settings=True)
def diagnostics(inv: Invocation) -> CommandResult:
    from src import diagnostics as bundle

    target = inv.resolve_path(inv.args.out) if inv.args.out else None
    try:
        written = bundle.write_bundle(target, source="cli")
    except OSError as e:
        raise StorageError.from_exception(e, target) from e
    return CommandResult(data={"path": written}, text=inv.t("diagnostics_written", path=written))


__all__: Sequence[str] = [
    "DESCRIBE_TOPICS",
    "default_config_text",
    "describe_config",
    "describe_errors",
    "describe_exit_codes",
    "describe_schemas",
    "describe_slices",
    "describe_sports",
    "legacy_config_text",
    "toml_value",
]
