"""
`trust`: HTTPS'i yeniden imzalayan ağlarda (HTTPS tarayan antivirüs, kurumsal TLS denetimi) güvenilen ek
sertifikalar (#225; sofascore_scraper/client/trust.py). Web arayüzü olmayan sunucular için.

    ssc trust list                    depodaki sertifikalar (`<config>/ca/*.pem`)
    ssc trust show                    ağın şu an sunduğu sertifika ve ona güvenilip güvenilemeyeceği
    ssc trust add --sha256 <iz>       sunulan sertifikaya güven; parmak izi `show`un gösterdiğiyle aynı olmalı
    ssc trust add --yes               sunulan sertifikaya, ne olduğunu yazdırarak güven
    ssc trust remove <iz>             depodan çıkar

  * Komut soru sormaz: `add`, `--sha256` ya da `--yes` olmadan `confirmation_required` ile çıkar ve neye
    güveneceğini söyler.
  * `add` ağı kendisi yoklar; sunulan sertifika verilen parmak iziyle eşleşmiyorsa ya da güvenilemiyorsa
    `certificate_changed` ile çıkar (çıkış kodu 1).
  * Yoklama HTTP isteği göndermez (TLS el sıkışması); yapılandırılmış bir proxy varsa yapılmaz.

Ağır içe aktarmalar işlevlerin içindedir (sofascore_scraper/cli/commands/__init__.py).
"""
from __future__ import annotations

import argparse
from typing import Any, Dict, List

from sofascore_scraper.cli.commands import CommandResult, Invocation, command, group
from sofascore_scraper.cli.output import Translator
from sofascore_scraper.errors import ConflictError, NotFoundError, UsageError

group("trust", help="ssc_help_cmd_trust")

# Yoklamanın nedeni (sofascore_scraper/client/trust.py REASON_*) → açıklamanın çeviri anahtarı
_REASON_TEXT = {
    "verified": "ssc_trust_reason_verified",
    "proxy": "ssc_trust_reason_proxy",
    "not_https": "ssc_trust_reason_not_https",
    "unreachable": "ssc_trust_reason_unreachable",
    "root_not_sent": "ssc_trust_reason_root_not_sent",
    "chain_unavailable": "ssc_trust_reason_chain_unavailable",
    "expired": "ssc_trust_reason_expired",
    "not_enough": "ssc_trust_reason_not_enough",
}


def _colons(sha256: str) -> str:
    return ":".join(sha256[i:i + 2] for i in range(0, len(sha256), 2)).upper()


def _who(name: Dict[str, Any]) -> str:
    common, organization = name.get("common_name"), name.get("organization")
    if common and organization and organization != common:
        return f"{common} ({organization})"
    return common or organization or "?"


def _lines(inv: Invocation, certificate: Dict[str, Any]) -> List[str]:
    lines = [
        inv.t("ssc_trust_subject", name=_who(certificate["subject"])),
        inv.t("ssc_trust_issuer", name=_who(certificate["issuer"])),
        inv.t("ssc_trust_sha256", sha256=_colons(certificate["sha256"])),
        inv.t("ssc_trust_not_after", date=certificate["not_after"]),
    ]
    if certificate["expired"]:
        lines.append(inv.t("ssc_trust_expired"))
    return lines


def _reason(inv: Invocation, probe: Dict[str, Any]) -> str:
    text = inv.t(_REASON_TEXT.get(probe["reason"], "ssc_trust_reason_unreachable"))
    if probe["reason"] == "root_not_sent" and probe["leaf"]:
        text += " " + inv.t("ssc_trust_signed_by", name=_who(probe["leaf"]["issuer"]))
    if probe["error"] and probe["reason"] in ("unreachable", "not_enough"):
        text += f" ({probe['error']})"
    return text


# --- list -----------------------------------------------------------------------------------------------

@command("trust list", help="ssc_help_cmd_trust_list")
def trust_list(inv: Invocation) -> CommandResult:
    from sofascore_scraper.client import trust

    items = [item.to_dict() for item in trust.stored()]
    data = {"directory": str(trust.ca_dir()), "certificates": items}
    if not items:
        return CommandResult(data=data, text=inv.t("ssc_trust_none", path=str(trust.ca_dir())))
    blocks = ["\n".join([item["file"], *("  " + line for line in _lines(inv, item))]) for item in items]
    return CommandResult(data=data, text="\n".join(blocks))


# --- show -----------------------------------------------------------------------------------------------

@command("trust show", help="ssc_help_cmd_trust_show", settings=True)
def trust_show(inv: Invocation) -> CommandResult:
    from sofascore_scraper.client import trust

    probe = trust.probe().to_dict()
    lines = [inv.t("ssc_trust_host", host=probe["host"], port=probe["port"])]
    if probe["can_trust"]:
        lines += [inv.t("ssc_trust_presented"), *("  " + line for line in _lines(inv, probe["presented"])),
                  inv.t("ssc_trust_how", sha256=probe["presented"]["sha256"])]
    else:
        lines.append(_reason(inv, probe))
    return CommandResult(data=probe, text="\n".join(lines))


# --- add ------------------------------------------------------------------------------------------------

def _add_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--sha256", metavar="FINGERPRINT", help=t("ssc_help_trust_sha256"))
    parser.add_argument("--yes", action="store_true", help=t("ssc_help_trust_yes"))


@command("trust add", help="ssc_help_cmd_trust_add", configure=_add_arguments, settings=True)
def trust_add(inv: Invocation) -> CommandResult:
    from sofascore_scraper.client import trust

    wanted = None
    if inv.args.sha256:
        try:
            wanted = trust.normalize_fingerprint(inv.args.sha256)
        except ValueError as e:
            raise UsageError(f"--sha256: {e}") from None
    elif not inv.args.yes:
        probe = trust.probe().to_dict()
        if probe["can_trust"]:
            found = "{} (sha256 {})".format(_who(probe["presented"]["subject"]), probe["presented"]["sha256"])
            message = (f"the network presents {found}; whoever holds its key can read and change what the app "
                       "downloads. Add --sha256 with that fingerprint, or --yes")
        else:
            message = "nothing can be trusted now ({}); see `ssc trust show`".format(probe["reason"])
        raise UsageError(message, {"probe": probe}, code="confirmation_required")
    try:
        stored, _probe = trust.trust_presented(wanted)
    except trust.TrustRefused as e:
        probe = e.probe.to_dict()
        if e.reason == trust.REASON_CHANGED:
            message = "the network presents another certificate (sha256 {}), not the one given".format(
                probe["presented"]["sha256"])
        else:
            message = "the certificate cannot be trusted: " + _reason(inv, probe)
        raise ConflictError(message, {"reason": e.reason, "probe": probe}, code="certificate_changed") from None
    after = trust.probe().to_dict()
    data = {"stored": stored.to_dict(), "directory": str(trust.ca_dir()), "verified": after["verified"],
            "browser_supported": trust.browser_supported()}
    lines = [inv.t("ssc_trust_added", file=stored.file, path=str(trust.ca_dir())),
             *("  " + line for line in _lines(inv, stored.to_dict())),
             inv.t("ssc_trust_verified" if after["verified"] else "ssc_trust_not_verified")]
    notes = [inv.t("ssc_trust_restart_note")]
    if not data["browser_supported"]:
        notes.append(inv.t("ssc_trust_browser_note"))
    return CommandResult(data=data, text="\n".join(lines), notes=notes, exit_code=0 if after["verified"] else 1)


# --- remove ---------------------------------------------------------------------------------------------

def _remove_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("sha256", metavar="FINGERPRINT", help=t("ssc_help_trust_fingerprint"))


@command("trust remove", help="ssc_help_cmd_trust_remove", configure=_remove_arguments)
def trust_remove(inv: Invocation) -> CommandResult:
    from sofascore_scraper.client import trust

    try:
        wanted = trust.normalize_fingerprint(inv.args.sha256)
    except ValueError as e:
        raise UsageError(f"FINGERPRINT: {e}") from None
    if not trust.remove(wanted):
        raise NotFoundError("no trusted certificate has this fingerprint", {"sha256": wanted})
    return CommandResult(
        data={"removed": wanted, "directory": str(trust.ca_dir())},
        text=inv.t("ssc_trust_removed", sha256=_colons(wanted)), notes=[inv.t("ssc_trust_restart_note")],
    )
