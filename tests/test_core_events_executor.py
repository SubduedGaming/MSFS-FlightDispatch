import threading

import pytest

from skydispatch.core.events import EventBus
from skydispatch.core.executor import SerialExecutor


def test_eventbus_emit_and_unsubscribe():
    bus, seen = EventBus(), []
    off = bus.on("x", lambda a, b: seen.append((a, b)))
    bus.emit("x", 1, 2)
    off()
    bus.emit("x", 3, 4)
    assert seen == [(1, 2)]


def test_eventbus_failing_handler_does_not_block_others():
    bus, seen = EventBus(), []
    bus.on("x", lambda: 1 / 0)
    bus.on("x", lambda: seen.append("ok"))
    bus.emit("x")
    assert seen == ["ok"]


def test_executor_runs_on_one_thread_and_returns_values():
    ex = SerialExecutor()
    try:
        names = {ex.run(lambda: threading.current_thread().name) for _ in range(5)}
        assert names == {"skydispatch-engine"}
        assert ex.run(lambda: 41 + 1) == 42
        with pytest.raises(ValueError):
            ex.run(lambda: (_ for _ in ()).throw(ValueError("boom")))
        assert ex.run(lambda: ex.run(lambda: "nested")) == "nested"      # no deadlock when re-entered
    finally:
        ex.close()


def test_executor_post_keeps_order():
    ex, out = SerialExecutor(), []
    try:
        for i in range(20):
            ex.post(lambda i=i: out.append(i))
        ex.run(lambda: None)
        assert out == list(range(20))
    finally:
        ex.close()


def test_event_matches_the_qt_signal_surface():
    from skydispatch.core.events import Event
    ev, seen = Event(), []
    f = lambda a, b: seen.append((a, b))
    ev.connect(f)
    ev.connect(lambda a, b: 1 / 0)                 # a failing handler does not stop the others
    ev.emit(1, 2)
    ev.disconnect(f)
    ev.emit(3, 4)
    assert seen == [(1, 2)]
