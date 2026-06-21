"""Media invoker: pull frames from a stream, or push frames to one.

The continuous-binary plane (audio/video), distinct from the request/response
invokers and from the event/subscription plane (MQTT, SSE, Pub/Sub). "Stream"
is overloaded: event subscriptions are streams too, but they carry discrete
structured messages and are bindable WoT Events. Media is continuous, encoded,
session oriented, and reached by reference; the runtime never binds it as a
property value. This invoker opens the session off the event loop and yields
decoded frames as an async iterator (consume), or pushes frames to an ingest
target (produce). The control around a stream (generate stream, get ingest uri)
stays on the request/response plane.

Backends are blocking (FFmpeg/PyAV, later GStreamer). The invoker runs them in a
worker thread and bridges frames back through a bounded queue. Backpressure is a
policy: ``latest`` sheds all but the newest frame (live video, low latency),
``all`` paces the source to the consumer (lossless). The surface is the same for
one source or many: drive a fleet with ``asyncio.gather`` over several
``frames()`` iterators.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator
from typing import Any

from thingctx.auth import AuthRegistry, AuthStrategy, apply_media, redact_url
from thingctx.invokers.base import _AuthBinding
from thingctx.invokers.media.frame import Frame, MediaBackend
from thingctx.thing import WoTAction, WoTForm


class MediaError(Exception):
    """A media read/connection failure. Any credentials embedded in the source
    URL (userinfo, token query params) are redacted from the message, so media
    errors never leak secrets into logs or tracebacks."""


# Schemes whose hrefs route here directly. Sources whose href is an http(s) page
# are routed by the media hint instead (see ``handles``).
MEDIA_SCHEMES = ("rtsp", "rtsps", "srt", "rtmp", "rtmps", "webrtc")
_MEDIA_HINT = "x-thingctx-media"


def _media_hint(form: WoTForm) -> dict:
    """The form's media hint (``x-thingctx-media``) as a dict, or empty."""
    raw = getattr(form, "raw", {}) or {}
    hint = raw.get(_MEDIA_HINT)
    if isinstance(hint, dict):
        return hint
    if isinstance(hint, str):
        return {"source": hint}
    return {}


def is_media_form(form: WoTForm) -> bool:
    """Whether a form belongs to the media plane: a media scheme
    (``rtsp``/``webrtc``/...) or any form carrying the ``x-thingctx-media`` hint
    (e.g. an http(s) href that should decode frames rather than be fetched)."""
    return form.scheme in MEDIA_SCHEMES or bool(_media_hint(form))


class MediaInvoker(_AuthBinding):
    """Drives media forms. Selected for the media schemes, or for any form
    carrying a media hint. Exposes ``frames()`` (consume) and ``publish()``
    (produce), for both video and audio tracks; ``invoke`` is not the media
    surface and is rejected.

    Honors declared security through the transport neutral auth layer: it
    resolves each owner's schemes into neutral credential material (see
    :class:`_AuthBinding`) and maps it onto the source with ``apply_media``;
    URL userinfo, request headers, query tokens, or TLS. No auth logic lives in
    this transport."""

    scheme = "rtsp"
    schemes = MEDIA_SCHEMES

    def __init__(
        self,
        backends: list[MediaBackend] | None = None,
        *,
        max_queue: int = 4,
        backpressure: str = "latest",
        credentials: dict | None = None,
        timeout: float = 30.0,
        allow_insecure_oauth: bool = False,
        auth: AuthRegistry | None = None,
        extra_auth: list[AuthStrategy] | None = None,
    ) -> None:
        # Lazy default backends so importing this module never requires the
        # optional media dependencies.
        if backends is None:
            from thingctx.invokers.media.backends import ExtractorBackend, PyAVBackend

            backends = [ExtractorBackend(), PyAVBackend()]
        if backpressure not in ("latest", "all"):
            raise ValueError("backpressure must be 'latest' or 'all'")
        self._backends = list(backends)
        self._max_queue = max(1, max_queue)
        # "latest": shed all but the newest frame when the consumer falls behind
        # (live media keeps latency low). "all": pace the source to the consumer
        # so no frame is lost (finite or lossless sources).
        self._backpressure = backpressure
        self._init_auth(
            credentials=credentials,
            auth=auth,
            extra_auth=extra_auth,
            timeout=timeout,
            allow_insecure_oauth=allow_insecure_oauth,
        )

    def handles(self, form: WoTForm) -> bool:
        """Whether this invoker should drive ``form``: a media scheme, or a
        media hint on an otherwise http(s) form (e.g. a page resolved by an
        extractor)."""
        return form.scheme in self.schemes or bool(_media_hint(form))

    async def invoke(self, action: WoTAction, form: WoTForm, arguments: dict[str, Any]) -> Any:
        raise TypeError(
            "MediaInvoker has no request/response surface; use frames() to "
            "consume or publish() to produce media."
        )

    def _pick(self, url: str, hint: dict) -> MediaBackend:
        for backend in self._backends:
            if backend.can_open(url, hint):
                return backend
        raise LookupError(f"no media backend for {url!r} (hint={hint!r})")

    async def frames(
        self,
        action: WoTAction,
        form: WoTForm,
        arguments: dict[str, Any] | None = None,
        *,
        track: str = "video",
    ) -> AsyncIterator[Frame]:
        """Open the form's media source and yield decoded frames for ``track``
        (``video`` or ``audio``). Blocking decode runs in a worker thread;
        frames cross back through a bounded queue under the backpressure
        policy."""
        if track not in ("video", "audio"):
            raise ValueError("track must be 'video' or 'audio'")
        url, _ = form.fill(arguments or {})
        hint = _media_hint(form)
        backend = self._pick(url, hint)
        options = {**hint, "track": track}
        # Resolve the owning Thing's declared security and hand the backend a
        # neutral auth plan; the backend maps it to its engine (URL userinfo for
        # a decoder, account login for the extractor). Absent declared security,
        # no plan is attached.
        creds = await self._resolve_credentials(getattr(action, "thing_id", None))
        if creds:
            plan = apply_media(creds)
            if plan.has_credentials:
                options["auth"] = plan
        async for frame in self._pump(backend.read, url, options):
            yield frame

    async def publish(
        self,
        action: WoTAction,
        form: WoTForm,
        frames: AsyncIterator[Frame],
        arguments: dict[str, Any] | None = None,
        *,
        track: str = "video",
    ) -> None:
        """Push an async iterator of frames to the form's ingest target (a URL
        or a file). The mirror of ``frames()``. The consumer produces frames on
        the event loop; a worker thread encodes and muxes them off it; the two
        are paced through a bounded queue."""
        if track not in ("video", "audio"):
            raise ValueError("track must be 'video' or 'audio'")
        url, _ = form.fill(arguments or {})
        hint = _media_hint(form)
        backend = self._pick(url, hint)
        options = {**hint, "track": track}
        creds = await self._resolve_credentials(getattr(action, "thing_id", None))
        if creds:
            plan = apply_media(creds)
            if plan.has_credentials:
                options["auth"] = plan
        await self._drain(backend.write, url, options, frames)

    async def _drain(self, write, target: str, options: dict, source: AsyncIterator[Frame]) -> None:
        """Bridge an async frame source to a blocking writer thread.

        Frames cross to the worker through a bounded queue; when it fills, the
        producer awaits a free slot, so the encoder paces the source (no frame
        is dropped). A worker error is re-raised on the event loop with
        credentials scrubbed from the message.
        """
        import queue as _queue

        loop = asyncio.get_running_loop()
        q: _queue.Queue = _queue.Queue(maxsize=self._max_queue)
        stop = threading.Event()
        done = object()
        error: list[BaseException] = []

        def _blocking_frames() -> Any:
            while True:
                item = q.get()
                if item is done:
                    return
                yield item

        def _worker() -> None:
            try:
                write(_blocking_frames(), target, options=options, stop=stop)
            except BaseException as exc:  # surface encode and connect errors to the caller
                # Scrub credentials the engine may echo from the target URL; do
                # not chain the original (its message can hold the raw URL).
                error.append(MediaError(f"{type(exc).__name__}: {redact_url(str(exc))}"))
            finally:
                stop.set()

        def _put(item: Any) -> None:
            # Block until a slot frees or the worker stops, so a dead writer
            # never wedges the producer on a full queue.
            while not stop.is_set():
                try:
                    q.put(item, timeout=0.1)
                    return
                except _queue.Full:
                    continue

        thread = threading.Thread(target=_worker, name="thingctx-media-pub", daemon=True)
        thread.start()
        sent_done = False
        try:
            async for frame in source:
                if stop.is_set():  # the writer stopped early
                    break
                await loop.run_in_executor(None, _put, frame)
            # Graceful end of stream; signal a drain. Do not set ``stop``, the
            # writer must flush every queued frame and the encoder's tail.
            if not stop.is_set():
                await loop.run_in_executor(None, q.put, done)
                sent_done = True
        except BaseException:
            # Consumer error or cancellation; ask the writer to stop promptly.
            stop.set()
            raise
        finally:
            if not sent_done:
                # Unblock the worker's get() on the abnormal path.
                with contextlib.suppress(Exception):
                    q.put_nowait(done)
            await loop.run_in_executor(None, thread.join)
            if error:
                raise error[0]

    async def _pump(self, read, url: str, options: dict) -> AsyncIterator[Frame]:
        """Run a blocking frame generator in a thread and yield its frames on
        the event loop.

        With ``backpressure="latest"`` the oldest queued frame is dropped when
        the consumer falls behind. With ``"all"`` a free-slot semaphore paces
        the worker to the consumer so no frame is lost. Errors and
        end-of-stream are control items that always reach the consumer.
        """
        loop = asyncio.get_running_loop()
        drop = self._backpressure == "latest"
        # One slot of headroom (beyond the frame budget) reserved for a control
        # item, so end of stream or error never has to evict a pending frame, in
        # either mode.
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue + 1)
        free = None if drop else threading.Semaphore(self._max_queue)
        stop = threading.Event()
        done = object()

        def _offer_frame_drop(frame: Frame) -> None:
            # "latest": keep at most max_queue frames (shed the oldest when the
            # consumer lags); the reserved slot is left free for a control item.
            if queue.qsize() >= self._max_queue:
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(frame)

        def _emit_frame(frame: Frame) -> None:
            if drop:
                loop.call_soon_threadsafe(_offer_frame_drop, frame)
                return
            # "all": block the worker until the consumer frees a slot.
            while not free.acquire(timeout=0.1):
                if stop.is_set():
                    return
            loop.call_soon_threadsafe(queue.put_nowait, frame)

        def _emit_control(item: Any) -> None:
            # The reserved slot guarantees this lands without evicting a frame.
            loop.call_soon_threadsafe(queue.put_nowait, item)

        def _worker() -> None:
            try:
                for frame in read(url, options=options, stop=stop):
                    if stop.is_set():
                        break
                    _emit_frame(frame)
            except BaseException as exc:  # surface decode and connect errors to the consumer
                # Re-raise as a MediaError with credentials scrubbed from the
                # message (the engine may echo the source URL, which can carry
                # userinfo or a token). Don't chain the original; its message
                # and attributes can hold the unredacted URL.
                _emit_control(MediaError(f"{type(exc).__name__}: {redact_url(str(exc))}"))
            finally:
                _emit_control(done)

        thread = threading.Thread(target=_worker, name="thingctx-media", daemon=True)
        thread.start()
        try:
            while True:
                item = await queue.get()
                if item is done:
                    return
                if isinstance(item, BaseException):
                    raise item
                if free is not None:
                    free.release()
                yield item
        finally:
            stop.set()
