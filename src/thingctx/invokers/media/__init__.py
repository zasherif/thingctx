"""Media invoker: the continuous-binary (audio/video) plane.

Heavier than the single-file invokers, so it is a package: the invoker
(:class:`MediaInvoker`), its data type (:class:`Frame`), the engine protocol
(:class:`MediaBackend`), and the blocking backends. Backends are imported lazily
by the invoker so the optional media dependencies are never required to import.
"""

from __future__ import annotations

from thingctx.invokers.media.frame import Frame, MediaBackend
from thingctx.invokers.media.invoker import (
    MEDIA_SCHEMES,
    MediaError,
    MediaInvoker,
    is_media_form,
)
from thingctx.invokers.media.sample import sample_frames

__all__ = [
    "MediaInvoker",
    "MediaError",
    "Frame",
    "MediaBackend",
    "MEDIA_SCHEMES",
    "is_media_form",
    "sample_frames",
]
