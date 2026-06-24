"""Blocking media backends for :class:`~thingctx.bindings.builtin.media.MediaBinding`.

Each backend opens a source and yields decoded :class:`Frame` objects. They run
in a worker thread, never on the event loop. Heavy dependencies (``av``,
``yt_dlp``) are imported lazily so this module imports without them installed.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterator
from urllib.parse import urlparse

from thingctx.auth import redact_url
from thingctx.bindings.builtin.media.frame import Frame, MediaBackend
from thingctx.contracts import implements

_PYAV_SCHEMES = ("rtsp", "rtsps", "srt", "rtmp", "rtmps", "http", "https", "file", "")
_NOT_PYAV_SOURCES = ("webrtc", "genicam")

# Output URL scheme to muxer for the publish path. A file target (no scheme)
# lets the muxer be inferred from the extension.
_OUTPUT_FORMATS = {
    "rtmp": "flv",
    "rtmps": "flv",
    "rtsp": "rtsp",
    "rtsps": "rtsp",
    "srt": "mpegts",
}


def _output_format(url: str) -> str | None:
    return _OUTPUT_FORMATS.get(urlparse(url).scheme)


class _RedactingHandler(logging.Handler):
    """A no-op handler that redacts credentials from a record in place. Attached
    to the ``libav`` logger, its ``handle()`` runs during propagation (before the
    host app's root handlers see the record) and scrubs any URL the message
    carries; it emits nothing itself, so it never suppresses or duplicates logs."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return
        redacted = redact_url(msg)
        if redacted != msg:
            record.msg = redacted
            record.args = ()


def _install_libav_redaction() -> None:
    """Attach the redacting handler to the ``libav`` logger (where PyAV routes
    FFmpeg output) so a raised log level can never print a credentialed URL.
    Idempotent; mutates records in place, so it scrubs without suppressing logs."""
    log = logging.getLogger("libav")
    if getattr(log, "_thingctx_redacted", False):
        return
    log.addHandler(_RedactingHandler())
    log._thingctx_redacted = True  # type: ignore[attr-defined]


@implements(MediaBackend)
class PyAVBackend:
    """Decode RTSP / HLS / RTMP / HTTP / MJPEG / SRT to frames via FFmpeg
    (PyAV), video (RGB) or audio (PCM) per the ``track`` option. Cannot handle
    WebRTC or GigE, those need a gateway or Aravis."""

    def can_open(self, url: str, hint: dict) -> bool:
        if hint.get("source") in _NOT_PYAV_SOURCES:
            return False
        return urlparse(url).scheme in _PYAV_SCHEMES

    def read(self, url: str, *, options: dict, stop: threading.Event) -> Iterator[Frame]:
        import av

        _install_libav_redaction()
        av_options = dict(options.get("av_options") or {})
        plan = options.get("auth")
        if plan is not None:
            # Map the neutral auth plan onto FFmpeg (URL userinfo, headers,
            # query, TLS). All credential-to-engine logic lives in the applier.
            from thingctx.auth import av_auth_options

            url, extra = av_auth_options(plan, url)
            av_options.update(extra)
        if urlparse(url).scheme in ("rtsp", "rtsps"):
            # TCP interleaving avoids UDP packet loss on most networks.
            av_options.setdefault("rtsp_transport", "tcp")

        track = options.get("track", "video")
        container = av.open(url, options=av_options, timeout=options.get("timeout"))
        try:
            decode = container.decode(audio=0) if track == "audio" else container.decode(video=0)
            for frame in decode:
                if stop.is_set():
                    break
                yield self._audio(frame) if track == "audio" else self._video(frame)
        finally:
            with contextlib.suppress(Exception):
                container.close()

    @staticmethod
    def _video(frame) -> Frame:  # noqa: ANN001
        return Frame(
            data=frame.to_ndarray(format="rgb24"),
            kind="video",
            pts=float(frame.time) if frame.time is not None else None,
            width=frame.width,
            height=frame.height,
            encoding="rgb24",
        )

    @staticmethod
    def _audio(frame) -> Frame:  # noqa: ANN001
        layout = getattr(frame, "layout", None)
        return Frame(
            data=frame.to_ndarray(),
            kind="audio",
            pts=float(frame.time) if frame.time is not None else None,
            sample_rate=frame.sample_rate,
            channels=len(layout.channels) if layout else None,
            encoding="pcm",
        )

    def write(
        self, frames: Iterator[Frame], target: str, *, options: dict, stop: threading.Event
    ) -> None:
        """Encode and mux ``frames`` to ``target`` (an ingest URL or a file). The
        muxer is chosen from the URL scheme (or the file extension); credentials
        in the plan are applied to the target URL."""
        import av

        _install_libav_redaction()
        track = options.get("track", "video")
        if track != "video":
            raise NotImplementedError("PyAVBackend.write() supports the video track only")

        av_options = dict(options.get("av_options") or {})
        plan = options.get("auth")
        if plan is not None:
            from thingctx.auth import av_auth_options

            target, extra = av_auth_options(plan, target)
            av_options.update(extra)
        if urlparse(target).scheme in ("rtsp", "rtsps"):
            av_options.setdefault("rtsp_transport", "tcp")

        fmt = options.get("format") or _output_format(target)
        container = av.open(target, mode="w", format=fmt, options=av_options)
        try:
            self._write_video(container, frames, options, stop)
        finally:
            with contextlib.suppress(Exception):
                container.close()

    @staticmethod
    def _write_video(  # noqa: ANN001
        container, frames: Iterator[Frame], options: dict, stop: threading.Event
    ) -> None:
        """Encode frames with the configured codec and mux them. The stream is
        created from the first frame's dimensions; pts come from a fixed frame
        rate, so a producer need not supply timing."""
        import fractions

        import av
        import numpy as np

        fps = int(options.get("fps", 30) or 30)
        codec = options.get("video_codec", "libx264")
        stream = None
        time_base = fractions.Fraction(1, fps)
        i = 0
        for fr in frames:
            if stop.is_set():
                break
            arr = np.ascontiguousarray(fr.data)
            if stream is None:
                h, w = arr.shape[:2]
                stream = container.add_stream(codec, rate=fps)
                stream.width = w
                stream.height = h
                stream.pix_fmt = options.get("pix_fmt", "yuv420p")
                if options.get("bitrate"):
                    stream.bit_rate = int(options["bitrate"])
                # Low latency defaults for live ingest; override via hint.
                stream.options = {
                    "preset": options.get("preset", "veryfast"),
                    "tune": options.get("tune", "zerolatency"),
                }
            src_fmt = fr.encoding if fr.encoding in ("rgb24", "bgr24", "gray") else "rgb24"
            vframe = av.VideoFrame.from_ndarray(arr, format=src_fmt)
            vframe.pts = i
            vframe.time_base = time_base
            for packet in stream.encode(vframe):
                container.mux(packet)
            i += 1
        if stream is not None:
            for packet in stream.encode(None):  # flush the encoder
                container.mux(packet)


@implements(MediaBackend)
class ExtractorBackend(PyAVBackend):
    """Resolve a web page URL to a direct media URL with yt-dlp, then decode it
    with PyAV. yt-dlp covers hundreds of sites, so a new source needs a TD, not
    new code here. Works for both recorded and live (HLS) media.

    Selected by a declared media hint (``resolve: "page"``), never by hostname,
    so the runtime carries no per site knowledge. ``source: "youtube"`` is kept
    as an alias for the same intent."""

    def can_open(self, url: str, hint: dict) -> bool:
        return hint.get("resolve") == "page" or hint.get("source") == "youtube"

    def _resolve(self, url: str, options: dict) -> str:
        import yt_dlp

        fmt = options.get("format", "best[protocol^=http]")
        ydl_opts = {"format": fmt, "quiet": True, "no_warnings": True}
        plan = options.get("auth")
        if plan is not None:
            # Account login for sites that gate content behind one.
            from thingctx.auth import ytdlp_auth_options

            ydl_opts.update(ytdlp_auth_options(plan))
        # Cookie-based access (the reliable path for private/members content) is
        # an extractor option, not a credential: a cookie file or a browser to
        # read cookies from, declared on the form's media hint.
        if options.get("cookiefile"):
            ydl_opts["cookiefile"] = options["cookiefile"]
        if options.get("cookies_from_browser"):
            ydl_opts["cookiesfrombrowser"] = (options["cookies_from_browser"],)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return info["url"]

    def read(self, url: str, *, options: dict, stop: threading.Event) -> Iterator[Frame]:
        resolved = self._resolve(url, options)
        yield from super().read(resolved, options=options, stop=stop)
