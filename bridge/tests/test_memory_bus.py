"""memory_bus.py: publish/subscribe, prefix subscriptions, thread-safe delivery onto a bound loop,
services, the recent ring, and a subscriber that raises."""

from __future__ import annotations

import asyncio
import threading

from cc_buddy_bridge.memory_bus import RECENT_PER_TOPIC, SERVICES, TOPICS, MemoryBus


def test_publish_reaches_exact_and_prefix_subscribers_only() -> None:
    bus = MemoryBus(clock=lambda: 1.0)
    got: list[tuple[str, dict]] = []
    bus.subscribe("/buddy/memory/lesson", lambda t, m: got.append(("exact", m)))
    bus.subscribe("/buddy/memory/*", lambda t, m: got.append(("prefix", m)))
    bus.subscribe("/buddy/state", lambda t, m: got.append(("state", m)))
    event = bus.publish("/buddy/memory/lesson", {"action": "hint"})
    assert event.time == 1.0 and event.msg == {"action": "hint"}
    assert got == [("exact", {"action": "hint"}), ("prefix", {"action": "hint"})]
    assert bus.published == 1


def test_unsubscribe_stops_delivery() -> None:
    bus = MemoryBus()
    got: list[dict] = []
    sub = bus.subscribe("/buddy/state", lambda t, m: got.append(m))
    bus.publish("/buddy/state", {"state": "wake"})
    bus.unsubscribe(sub)
    bus.publish("/buddy/state", {"state": "idle"})
    assert got == [{"state": "wake"}]


def test_a_subscriber_that_raises_is_logged_and_the_others_still_run(caplog) -> None:
    bus = MemoryBus()
    got: list[dict] = []

    def bad(t, m):
        raise RuntimeError("boom")
    bus.subscribe("/buddy/state", bad)
    bus.subscribe("/buddy/state", lambda t, m: got.append(m))
    bus.publish("/buddy/state", {"state": "wake"})
    assert got == [{"state": "wake"}]
    assert any("subscriber failed" in r.getMessage() for r in caplog.records)


def test_publish_from_another_thread_delivers_on_the_bound_loop() -> None:
    async def go():
        bus = MemoryBus()
        bus.bind_loop(asyncio.get_running_loop())
        seen: list[tuple[str, dict]] = []
        done = asyncio.Event()

        def on_event(t, m):
            seen.append((threading.current_thread().name, m))
            done.set()
        bus.subscribe("/buddy/state", on_event)
        threading.Thread(target=bus.publish, args=("/buddy/state", {"state": "thinking"}), name="audio").start()
        await asyncio.wait_for(done.wait(), 2.0)
        assert seen == [(threading.current_thread().name, {"state": "thinking"})]   # the loop thread, not "audio"
    asyncio.run(go())


def test_an_async_subscriber_is_scheduled_on_the_loop() -> None:
    async def go():
        bus = MemoryBus()
        bus.bind_loop(asyncio.get_running_loop())
        seen: list[dict] = []

        async def on_event(t, m):
            seen.append(m)
        bus.subscribe("/buddy/state", on_event)
        bus.publish("/buddy/state", {"state": "wake"})
        await asyncio.sleep(0.01)
        assert seen == [{"state": "wake"}]
    asyncio.run(go())


def test_services_are_awaited_and_missing_ones_raise() -> None:
    async def go():
        bus = MemoryBus()

        async def recall(args):
            return {"results": [args["query"]]}
        bus.register_service("/buddy/memory/recall", recall)
        assert bus.has_service("/buddy/memory/recall")
        assert await bus.call_service("/buddy/memory/recall", {"query": "mug"}) == {"results": ["mug"]}
        try:
            await bus.call_service("/nope", {})
        except KeyError:
            return
        raise AssertionError("missing service did not raise")
    asyncio.run(go())


def test_recent_keeps_a_bounded_ring_per_topic() -> None:
    bus = MemoryBus()
    for i in range(RECENT_PER_TOPIC + 5):
        bus.publish("/buddy/state", {"i": i})
    recent = bus.recent("/buddy/state")
    assert len(recent) == RECENT_PER_TOPIC and recent[-1].msg == {"i": RECENT_PER_TOPIC + 4}
    assert bus.recent("/buddy/memory/lesson") == []
    assert bus.recent("/buddy/state", 2)[0].msg == {"i": RECENT_PER_TOPIC + 3}


def test_topic_and_service_catalogue_is_ros_shaped() -> None:
    assert all(t.startswith("/") and "/" in typ for t, typ in TOPICS.items())
    assert "/buddy/memory/recall" in SERVICES
    assert MemoryBus().topics() == TOPICS and MemoryBus().services() == SERVICES
