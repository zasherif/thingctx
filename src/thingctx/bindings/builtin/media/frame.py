"""Media data type and backend protocol.

The continuous-binary plane (audio/video), distinct from the event /
subscription plane (MQTT, SSE, Pub/Sub) which carries discrete structured
messages. The shared data type (:class:`Frame`) and the engine protocol
(:class:`MediaBackend`) live here so backends never import the binding.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Frame:
    """One decoded media unit; a video frame or a block of audio samples.

    ``data`` is an ndarray (pixels for video, PCM samples for audio) or encoded
    bytes (e.g. ``jpeg``). ``pts`` is the presentation time in seconds when the
    source provides it. The video fields (``width``/``height``) and audio fields
    (``sample_rate``/``channels``) are populated per ``kind``.
    """

    data: Any
    kind: str = "video"  # "video" | "audio"
    pts: float | None = None
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    encoding: str = "rgb24"  # video: rgb24/jpeg/... ; audio: pcm_s16/...
    meta: dict = field(default_factory=dict)


@runtime_checkable
class MediaBackend(Protocol):
    """A media engine. ``read`` is a blocking generator of frames; ``write``
    pushes frames to an ingest target. Both run off the event loop. The track
    to decode/produce (``video`` or ``audio``) is passed in ``options``."""

    def can_open(self, url: str, hint: dict) -> bool:
        """Whether this backend handles the given source."""

    def read(self, url: str, *, options: dict, stop: threading.Event) -> Iterator[Frame]:
        """Yield frames until exhausted or ``stop`` is set."""

    def write(
        self,
        frames: Iterator[Frame],
        target: str,
        *,
        options: dict,
        stop: threading.Event,
    ) -> None:
        """Push frames to ``target`` until exhausted or ``stop`` is set."""
