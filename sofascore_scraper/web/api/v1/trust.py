"""
API v1: güvenilen ek sertifikalar (HTTPS'i yeniden imzalayan ağlar; #225, sofascore_scraper/client/trust.py).

    GET    /api/v1/trust            depodaki sertifikalar (`<config>/ca/*.pem`)
    POST   /api/v1/trust/probe      ağın şu an sunduğu zincir ve ona güvenilip güvenilemeyeceği
    POST   /api/v1/trust            sunulan sertifikaya güven (gövdede kullanıcının gördüğü parmak izi), sonra
                                    bağlantı denetimi
    DELETE /api/v1/trust/{fingerprint}   depodan çıkar (SHA-256 parmak izi)

Bunlar güvenlik kararlarıdır: sertifikanın anahtarını elinde tutan, uygulamanın indirdiği her şeyi okuyabilir ve
değiştirebilir. Bütün `/api` yolları gibi belirteç ve kaynak (origin) denetiminden geçerler (web/app.py ara
katmanı). Sunucu, güvenilecek sertifikayı istemciden ALMAZ: ağı kendisi yoklar ve yalnızca o an sunulan
sertifika, parmak izi kullanıcının onayladığıyla aynıysa yazılır (aksi halde 409 `certificate_changed`).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field

from sofascore_scraper.errors import ConflictError, NotFoundError, UsageError
from sofascore_scraper.web.api.v1 import PageInfo
from sofascore_scraper.web.api.v1.meta import StatusCheck, check_sofascore
from sofascore_scraper.web.errors import error_responses

logger = logging.getLogger("WebAPI")
router = APIRouter(tags=["trust"])

Fingerprint = Path(
    pattern=r"^[0-9a-fA-F:]{64,95}$", description="SHA-256 fingerprint of the certificate (hex; colons allowed).",
)


class CertificateName(BaseModel):
    common_name: Optional[str] = None
    organization: Optional[str] = None


class CertificateInfo(BaseModel):
    """An X.509 certificate as shown to the user. `sha256` is the fingerprint of the whole certificate."""

    subject: CertificateName
    issuer: CertificateName
    sha256: str = Field(description="Lower-case hex, 64 digits.")
    not_before: str = Field(description="ISO-8601, UTC.")
    not_after: str = Field(description="ISO-8601, UTC.")
    expired: bool
    is_ca: bool = Field(description="A CA certificate (basicConstraints CA).")
    self_signed: bool = Field(description="Subject and issuer are the same: a root certificate.")


class TrustedCertificate(CertificateInfo):
    file: str = Field(description="File name in the `ca` folder of the config directory.")


class TrustListResponse(BaseModel):
    data: List[TrustedCertificate]
    page: PageInfo


TrustReason = Literal[
    "verified", "proxy", "not_https", "unreachable", "root_not_sent", "chain_unavailable", "expired", "not_enough",
]


class TrustProbe(BaseModel):
    """What the network presents for the API host right now. Read without verifying; no HTTP request is sent."""

    host: str
    port: int
    verified: bool = Field(description="The chain verifies with the current trust: nothing needs to be trusted.")
    chain_length: int = Field(description="Certificates the server sent; 0 when none could be read.")
    leaf: Optional[CertificateInfo] = Field(default=None, description="The server's own certificate.")
    presented: Optional[CertificateInfo] = Field(
        default=None, description="The certificate that would be trusted: the top-most CA certificate the "
                                  "server sent. Null unless `can_trust`.",
    )
    can_trust: bool
    reason: Optional[TrustReason] = Field(
        default=None,
        description="Why nothing can be trusted: `verified` (not needed), `proxy` (a proxy is configured; the "
                    "probe would bypass it), `not_https`, `unreachable`, `root_not_sent` (only the server's own "
                    "certificate was sent: put the CA certificate into the `ca` folder), `chain_unavailable` "
                    "(this Python reads only the server's own certificate; 3.13+ reads the chain), `expired`, "
                    "`not_enough` (trusting the presented CA would still not verify the connection).",
    )
    error: Optional[str] = Field(default=None, description="The verification or connection error, in English.")
    browser_supported: bool = Field(
        description="Trusted certificates also reach the built-in browser on this platform (Linux: its NSS "
                    "database). False: the browser uses the trust store of the operating system.",
    )


class TrustProbeResponse(BaseModel):
    data: TrustProbe


class TrustProbeRequest(BaseModel):
    """What to look at. The host cannot be chosen: it is the configured API host."""

    model_config = ConfigDict(extra="forbid")

    target: Literal["sofascore"] = Field(description="`sofascore`: the host of the API base URL (`client.base_url`).")


class TrustRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: str = Field(
        pattern=r"^[0-9a-fA-F:]{64,95}$",
        description="The fingerprint of the certificate the user was shown (`presented.sha256` of the probe).",
    )


class TrustOutcome(BaseModel):
    stored: TrustedCertificate
    check: StatusCheck = Field(description="The connection check made right after storing, as `POST /status/check`.")
    browser_supported: bool
    browser_restart_needed: bool = Field(
        description="True: the built-in browser of this server could not be restarted and reads the certificate "
                    "only after the app restarts. Other processes (`ssc watch`) always read it at their next start.",
    )


class TrustResponse(BaseModel):
    data: TrustOutcome


class TrustRemoved(BaseModel):
    sha256: str
    browser_restart_needed: bool


class TrustRemoveResponse(BaseModel):
    data: TrustRemoved


def _probe_model(result: Any) -> TrustProbe:
    from sofascore_scraper.client import trust

    return TrustProbe(**result.to_dict(), browser_supported=trust.browser_supported())


def _fingerprint(value: str) -> str:
    from sofascore_scraper.client import trust

    try:
        return trust.normalize_fingerprint(value)
    except ValueError as e:
        raise UsageError(f"sha256: {e}.", {"sha256": value}) from None


def _restart_browser() -> bool:
    """Bu sürecin köprü tarayıcısını yeni depoyla yeniden başlatılmak üzere kapatır; True: yeniden başlatma gerekli."""
    from sofascore_scraper.client import bridge

    return not bridge.restart_for_trust_change()


@router.get("/trust", response_model=TrustListResponse, operation_id="listTrusted", summary="List trusted certificates")
def list_trusted() -> TrustListResponse:
    """
    The extra certificates the app trusts: every PEM certificate in the `ca` folder of the config directory,
    whether stored with `POST /trust` or put there by hand.
    """
    from sofascore_scraper.client import trust

    items = [TrustedCertificate(**item.to_dict()) for item in trust.stored()]
    return TrustListResponse(data=items, page=PageInfo(limit=len(items), next_cursor=None))


@router.post(
    "/trust/probe",
    response_model=TrustProbeResponse,
    operation_id="probeTrust",
    summary="Look at the certificate the network presents",
    responses=error_responses("forbidden_origin"),
)
def probe_trust(body: TrustProbeRequest) -> TrustProbeResponse:
    """
    Open a TLS connection to the API host without verifying it and report who signed what the network
    presents, whether it verifies with the current trust, and which certificate could be trusted. At most
    three TLS handshakes; no HTTP request is sent. A failure is a result (`can_trust: false` with a `reason`).
    """
    from sofascore_scraper.client import trust

    return TrustProbeResponse(data=_probe_model(trust.probe()))


@router.post(
    "/trust",
    response_model=TrustResponse,
    operation_id="trustCertificate",
    summary="Trust the certificate the network presents",
    responses=error_responses("invalid_request", "forbidden_origin", "certificate_changed"),
)
def trust_certificate(body: TrustRequest) -> TrustResponse:
    """
    Store the certificate the network presents now, so that the HTTP client and the built-in browser accept
    connections signed by it. Whoever holds that certificate's key can read and change what the app downloads.
    The server probes again and stores only what it sees itself: when the fingerprint is not the one given, or
    nothing can be trusted any more, the answer is 409 `certificate_changed` (`details.reason`,
    `details.presented_sha256`). Then the connection check runs and its outcome is returned.
    """
    from sofascore_scraper.client import trust

    try:
        stored, _probe = trust.trust_presented(_fingerprint(body.sha256))
    except trust.TrustRefused as e:
        presented = e.probe.presented.sha256 if e.probe.presented else None
        details: Dict[str, Any] = {"reason": e.reason, "presented_sha256": presented}
        raise ConflictError(
            "The network does not present the certificate that was confirmed, or it cannot be trusted.",
            details, code="certificate_changed",
        ) from None
    logger.warning("Certificate trusted through the API: sha256 %s", stored.certificate.sha256)
    restart_needed = _restart_browser()
    return TrustResponse(data=TrustOutcome(
        stored=TrustedCertificate(**stored.to_dict()),
        check=check_sofascore(),
        browser_supported=trust.browser_supported(),
        browser_restart_needed=restart_needed,
    ))


@router.delete(
    "/trust/{fingerprint}",
    response_model=TrustRemoveResponse,
    operation_id="removeTrusted",
    summary="Stop trusting a certificate",
    responses=error_responses("not_found", "forbidden_origin"),
)
def remove_trusted(fingerprint: str = Fingerprint) -> TrustRemoveResponse:
    """Remove the certificate from the `ca` folder. The HTTP client stops accepting it with the next request."""
    from sofascore_scraper.client import trust

    fingerprint = _fingerprint(fingerprint)
    if not trust.remove(fingerprint):
        raise NotFoundError("No trusted certificate has this fingerprint.", {"sha256": fingerprint})
    return TrustRemoveResponse(data=TrustRemoved(sha256=fingerprint, browser_restart_needed=_restart_browser()))
