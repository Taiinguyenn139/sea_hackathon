"""Request-local stream callbacks; never serialized in graph checkpoints."""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar

StreamSink = Callable[[str, dict], None]
stream_sink: ContextVar[StreamSink | None] = ContextVar("stream_sink", default=None)


def emit(kind, **data):
    sink = stream_sink.get()
    if sink:
        sink(kind, data)


@contextmanager
def stream_to(sink):
    token = stream_sink.set(sink)
    try:
        yield
    finally:
        stream_sink.reset(token)
