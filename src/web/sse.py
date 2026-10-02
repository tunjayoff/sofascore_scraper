"""
İş olaylarının SSE akışı: `GET /api/v1/jobs/{id}/events` (docs/design/02-services.md bölüm 6).

Her ileti bir iş olayıdır (src/jobs/model.py: JobEvent):

    id: <seq>                 işin kendi olay numarası (iş başına 1'den başlar)
    event: <tür>              started | phase | progress | log | failed | breaker | cancel_requested | finished
    data: {"job_id", "seq", "ts_ms", "type", "data"}

Kaldığı yerden sürdürme: standart `Last-Event-ID` başlığı ya da `?after=<seq>`. İstenen yer işin saklanan
olaylarından eskiyse (iş başına en yeni 2.000 olay tutulur) sunucu tek bir `stream.gap` olayı
(`{"job_id", "after", "oldest_seq"}`) gönderir ve akışı kapatır; istemci durumu `/jobs/{id}` ile yeniden
kurar. Bağlantıyı canlı tutmak için 15 saniyede bir yorum satırı gönderilir. İş bittiğinde, kalan olaylar
gönderildikten sonra akış kapanır.

Burada yalnızca iş olayları sunulur: canlı maç verisinin HTTP ucu yoktur (sahibin kararı; canlı veri sink'lerle
çıkar).

İletileri bu modül çerçeveler (`id:` ve yorum satırları üzerinde tam denetim); yanıtın kendisi sse-starlette'in
EventSourceResponse'udur. Ona üç iş kalır: canlı tutma satırını zamanında göndermek, istemci koptuğunda akışı
durdurmak ve sunucu kapanırken açık akışları bitirmek (yoksa uvicorn, Ctrl+C'den sonra açık bir akışın
kapanmasını süresiz bekler). Depo okumaları iş parçacığı havuzunda yapılır; olay döngüsü hiçbir SQLite
çağrısında beklemez.
"""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, AsyncIterator, List, Mapping, Optional

from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from src.errors import NotSupportedError

if TYPE_CHECKING:
    from src.jobs.manager import JobManager
    from src.jobs.model import JobEvent

MEDIA_TYPE = "text/event-stream"
KEEPALIVE_SECONDS = 15.0  # bu aralıkla bir yorum satırı gönderilir
KEEPALIVE_COMMENT = "keep-alive"
POLL_SECONDS = 0.25  # yeni olaylar bu aralıkla yoklanır
GAP_EVENT = "stream.gap"


def format_event(event: str, data: Any, *, event_id: Optional[int] = None) -> bytes:
    """Bir SSE iletisi. `data` tek satır JSON'dur (satır sonu içermez), bu yüzden tek `data:` satırı yeter."""
    lines: List[str] = []
    if event_id is not None:
        lines.append(f"id: {int(event_id)}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'), default=str)}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def format_comment(text: str = "") -> bytes:
    """Yorum satırı: istemci yok sayar, bağlantı canlı kalır."""
    return f": {text}\n\n".encode("utf-8")


def event_payload(event: "JobEvent") -> Mapping[str, Any]:
    return {"job_id": event.job_id, "seq": event.seq, "ts_ms": event.ts_ms, "type": event.type, "data": dict(event.data)}


def _read(manager: "JobManager", job_id: str, after: int) -> List["JobEvent"]:
    return list(manager.events(job_id, after=after, follow=False))


def _oldest_seq(manager: "JobManager", job_id: str) -> Optional[int]:
    first = manager.store.read_events(job_id, after=0, limit=1)
    return int(first[0]["seq"]) if first else None


def _finished(manager: "JobManager", job_id: str) -> bool:
    job = manager.get(job_id)
    return job is None or job.state.terminal


async def job_event_stream(
    manager: "JobManager", job_id: str, *, after: int = 0, poll: Optional[float] = None,
) -> AsyncIterator[bytes]:
    """
    İşin `after`dan sonraki olaylarını, iş bitene kadar SSE iletileri olarak üretir.

    after > 0 bir sürdürme isteğidir: aradaki olaylar artık saklanmıyorsa tek bir `stream.gap` olayı üretilir
    ve akış biter. after == 0 (yeni bağlantı) saklanan en eski olaydan başlar.
    """
    position = max(0, int(after))
    if position > 0:
        oldest = await run_in_threadpool(_oldest_seq, manager, job_id)
        if oldest is not None and oldest > position + 1:
            yield format_event(GAP_EVENT, {"job_id": job_id, "after": position, "oldest_seq": oldest})
            return
    # İlk bayt: başlıkların hemen gitmesini sağlar (istemci bağlantının açıldığını görür)
    yield format_comment("stream open")
    while True:
        events = await run_in_threadpool(_read, manager, job_id, position)
        for event in events:
            position = event.seq
            yield format_event(event.type, event_payload(event), event_id=event.seq)
        if events:
            continue
        if await run_in_threadpool(_finished, manager, job_id):
            # İş bitti: bitişten hemen önce yazılmış olaylar için son bir okuma
            for event in await run_in_threadpool(_read, manager, job_id, position):
                yield format_event(event.type, event_payload(event), event_id=event.seq)
            return
        await asyncio.sleep(max(0.01, float(POLL_SECONDS if poll is None else poll)))


def keep_alive() -> Any:
    """Canlı tutma iletisi: yalnızca bir yorum satırı (`: keep-alive`)."""
    from sse_starlette.event import ServerSentEvent

    return ServerSentEvent(comment=KEEPALIVE_COMMENT)


def job_event_response(
    manager: "JobManager", job_id: str, *, after: int = 0, poll: Optional[float] = None,
    keepalive: Optional[float] = None,
) -> Response:
    """
    İş olaylarının akış yanıtı (`text/event-stream`). İstemci koptuğunda ya da sunucu kapanırken akış durur.
    sse-starlette kurulu değilse NotSupportedError (501 `not_supported`).
    """
    try:
        from sse_starlette.sse import EventSourceResponse
    except ImportError:
        raise NotSupportedError(
            "Event streams need the sse-starlette package; follow the job with GET /api/v1/jobs/{id} instead.",
            {"package": "sse-starlette"},
        ) from None
    return EventSourceResponse(
        job_event_stream(manager, job_id, after=after, poll=poll),
        ping=KEEPALIVE_SECONDS if keepalive is None else keepalive,
        ping_message_factory=keep_alive,
    )


__all__ = [
    "GAP_EVENT",
    "KEEPALIVE_COMMENT",
    "KEEPALIVE_SECONDS",
    "MEDIA_TYPE",
    "POLL_SECONDS",
    "event_payload",
    "format_comment",
    "format_event",
    "job_event_response",
    "job_event_stream",
    "keep_alive",
]
