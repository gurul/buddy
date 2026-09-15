"""rosbridge.py: the v2 protocol over the MemoryBus, one real server per test on a free port.

Each op has a test. The bus is real; the client is `websockets`. Test 10 uses the reference
client, roslibpy, and is skipped when it is not installed (it is never a project dependency)."""

from __future__ import annotations

import asyncio
import json
import socket
import time

import pytest
from websockets.asyncio.client import connect

from cc_buddy_bridge.memory_bus import MemoryBus
from cc_buddy_bridge.rosbridge import RosbridgeServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _serve(bus: MemoryBus, **kw) -> RosbridgeServer:
    server = RosbridgeServer(bus, port=_free_port(), **kw)
    await server.start()
    return server


async def _recv(ws, timeout: float = 2.0) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


async def _send(ws, **op) -> None:
    await ws.send(json.dumps(op))


def _run(coro) -> None:
    asyncio.run(coro)


def test_subscribe_then_bus_publish_reaches_the_client() -> None:
    async def go():
        bus = MemoryBus()
        bus.bind_loop(asyncio.get_running_loop())
        server = await _serve(bus)
        try:
            async with connect(server.url) as ws:
                await _send(ws, op="subscribe", id="s1", topic="/buddy/state", type="buddy_msgs/AgentState")
                await asyncio.sleep(0.05)
                bus.publish("/buddy/state", {"state": "wake"})
                assert await _recv(ws) == {"op": "publish", "topic": "/buddy/state", "msg": {"state": "wake"}}
                assert server.clients == 1 and server.stats["sent"] == 1
        finally:
            await server.stop()
        assert server.clients == 0
    _run(go())


def test_prefix_subscription_receives_memory_topics_but_not_state() -> None:
    async def go():
        bus = MemoryBus()
        bus.bind_loop(asyncio.get_running_loop())
        server = await _serve(bus)
        try:
            async with connect(server.url) as ws:
                await _send(ws, op="subscribe", id="p", topic="/buddy/memory/*")
                await asyncio.sleep(0.05)
                bus.publish("/buddy/state", {"state": "wake"})
                bus.publish("/buddy/memory/lesson", {"action": "hint"})
                bus.publish("/buddy/memory/observation", {"thought": "the mug moved"})
                first, second = await _recv(ws), await _recv(ws)
                assert [m["topic"] for m in (first, second)] == ["/buddy/memory/lesson", "/buddy/memory/observation"]
                with pytest.raises(asyncio.TimeoutError):
                    await _recv(ws, 0.2)
        finally:
            await server.stop()
    _run(go())


def test_unsubscribe_stops_delivery_and_disconnect_clears_the_bus() -> None:
    async def go():
        bus = MemoryBus()
        bus.bind_loop(asyncio.get_running_loop())
        server = await _serve(bus)
        try:
            async with connect(server.url) as ws:
                await _send(ws, op="subscribe", id="a", topic="/buddy/state")
                await _send(ws, op="subscribe", id="b", topic="/buddy/memory/lesson")
                await asyncio.sleep(0.05)
                assert sum(len(v) for v in bus._subs.values()) == 2
                await _send(ws, op="unsubscribe", id="a", topic="/buddy/state")
                await asyncio.sleep(0.05)
                bus.publish("/buddy/state", {"state": "wake"})
                bus.publish("/buddy/memory/lesson", {"action": "step"})
                assert (await _recv(ws))["topic"] == "/buddy/memory/lesson"
                with pytest.raises(asyncio.TimeoutError):
                    await _recv(ws, 0.2)
            await asyncio.sleep(0.1)
            assert sum(len(v) for v in bus._subs.values()) == 0
            assert server.clients == 0
        finally:
            await server.stop()
    _run(go())


def test_inbound_publish_reaches_the_bus_only_for_known_topics() -> None:
    async def go():
        bus = MemoryBus()
        bus.bind_loop(asyncio.get_running_loop())
        got: list[tuple[str, dict]] = []
        bus.subscribe("/buddy/*", lambda t, m: got.append((t, m)))
        server = await _serve(bus)
        try:
            async with connect(server.url) as ws:
                await _send(ws, op="publish", topic="/buddy/memory/remember", msg={"text": "the mug is Sam's"})
                await _send(ws, op="publish", id="x", topic="/nope/topic", msg={"text": "no"})
                status = await _recv(ws)
                assert status["op"] == "status" and status["level"] == "error" and status["id"] == "x"
                await _send(ws, op="publish", topic="/buddy/state", msg="not an object")
                assert (await _recv(ws))["level"] == "error"
            await asyncio.sleep(0.05)
        finally:
            await server.stop()
        assert got == [("/buddy/memory/remember", {"text": "the mug is Sam's"})]
    _run(go())


def test_call_service_answers_with_service_response() -> None:
    async def go():
        bus = MemoryBus()
        bus.bind_loop(asyncio.get_running_loop())

        async def recall(args):
            return {"results": [{"title": args["query"]}]}

        async def broken(args):
            raise RuntimeError("worker down")
        bus.register_service("/buddy/memory/recall", recall)
        bus.register_service("/buddy/memory/broken", broken)
        server = await _serve(bus)
        try:
            async with connect(server.url) as ws:
                await _send(ws, op="call_service", id="c1", service="/buddy/memory/recall", args={"query": "mug"})
                reply = await _recv(ws)
                assert reply == {"op": "service_response", "service": "/buddy/memory/recall", "id": "c1",
                                 "result": True, "values": {"results": [{"title": "mug"}]}}
                await _send(ws, op="call_service", id="c2", service="/nowhere")
                reply = await _recv(ws)
                assert reply["result"] is False and reply["values"] == {"error": "no such service"} and reply["id"] == "c2"
                await _send(ws, op="call_service", id="c3", service="/buddy/memory/broken", args={})
                reply = await _recv(ws)
                assert reply["result"] is False and "worker down" in reply["values"]["error"]
        finally:
            await server.stop()
    _run(go())


def test_throttle_rate_drops_events_that_come_too_fast() -> None:
    async def go():
        bus = MemoryBus()
        bus.bind_loop(asyncio.get_running_loop())
        server = await _serve(bus)
        try:
            async with connect(server.url) as ws:
                await _send(ws, op="subscribe", id="t", topic="/buddy/state", throttle_rate=200)
                await asyncio.sleep(0.05)
                for i in range(5):
                    bus.publish("/buddy/state", {"i": i})
                got = [await _recv(ws)]
                try:
                    got.append(await _recv(ws, 0.3))
                except asyncio.TimeoutError:
                    pass
                assert 1 <= len(got) <= 2 and got[0]["msg"] == {"i": 0}
        finally:
            await server.stop()
    _run(go())


def test_a_client_that_never_reads_drops_its_own_messages_and_never_blocks_the_bus() -> None:
    async def go():
        bus = MemoryBus()
        bus.bind_loop(asyncio.get_running_loop())
        server = await _serve(bus, queue_size=3)
        try:
            async with connect(server.url) as ws:
                await _send(ws, op="subscribe", id="q", topic="/buddy/state")
                await asyncio.sleep(0.05)
                # Stall the writer so the queue fills: the client is alive but not draining.
                client = next(iter(server._clients))
                client.writer.cancel()
                await asyncio.gather(client.writer, return_exceptions=True)
                started = time.monotonic()
                for i in range(20):
                    bus.publish("/buddy/state", {"i": i})
                assert time.monotonic() - started < 1.0
                assert server.stats["dropped"] >= 17 and client.dropped == server.stats["dropped"]
                assert client.queue.qsize() == 3
                assert json.loads(client.queue.get_nowait())["msg"] == {"i": 17}   # the oldest went first
        finally:
            await server.stop()
    _run(go())


def test_malformed_json_gets_a_status_error_and_the_connection_stays_open() -> None:
    async def go():
        bus = MemoryBus()
        bus.bind_loop(asyncio.get_running_loop())
        server = await _serve(bus)
        try:
            async with connect(server.url) as ws:
                await ws.send("{not json")
                assert (await _recv(ws))["level"] == "error"
                await ws.send("[1, 2]")
                assert (await _recv(ws))["level"] == "error"
                await _send(ws, op="dance", id="d")
                status = await _recv(ws)
                assert status["level"] == "error" and "dance" in status["msg"] and status["id"] == "d"
                await _send(ws, op="subscribe", id="s", topic="/buddy/state")
                await asyncio.sleep(0.05)
                bus.publish("/buddy/state", {"state": "wake"})
                assert (await _recv(ws))["msg"] == {"state": "wake"}
        finally:
            await server.stop()
    _run(go())


def test_advertise_unadvertise_and_set_level_are_accepted_silently() -> None:
    async def go():
        bus = MemoryBus()
        bus.bind_loop(asyncio.get_running_loop())
        server = await _serve(bus)
        try:
            async with connect(server.url) as ws:
                await _send(ws, op="advertise", id="a", topic="/buddy/memory/remember", type="buddy_msgs/Remember")
                await _send(ws, op="set_level", level="error")
                await _send(ws, op="unadvertise", id="a", topic="/buddy/memory/remember")
                with pytest.raises(asyncio.TimeoutError):
                    await _recv(ws, 0.2)
                await _send(ws, op="subscribe", id="u", topic="/not/a/topic")
                status = await _recv(ws)
                assert status["level"] == "error" and "subscribed anyway" in status["msg"]
        finally:
            await server.stop()
    _run(go())


def test_the_reference_client_roslibpy_receives_a_message() -> None:
    roslibpy = pytest.importorskip("roslibpy")
    import threading

    bus = MemoryBus()
    port = _free_port()
    ready = threading.Event()
    stop = threading.Event()
    holder: dict = {}

    def run_server():
        async def main():
            bus.bind_loop(asyncio.get_running_loop())
            server = RosbridgeServer(bus, port=port)
            await server.start()
            holder["server"] = server
            ready.set()
            while not stop.is_set():
                await asyncio.sleep(0.05)
            await server.stop()
        asyncio.run(main())

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    assert ready.wait(5)
    got: list[dict] = []
    seen = threading.Event()
    client = roslibpy.Ros(host="127.0.0.1", port=port)
    client.run(timeout=5)
    try:
        assert client.is_connected
        topic = roslibpy.Topic(client, "/buddy/state", "buddy_msgs/AgentState")
        topic.subscribe(lambda m: (got.append(m), seen.set()))
        time.sleep(0.3)
        bus.publish("/buddy/state", {"state": "wake"})
        assert seen.wait(3), "roslibpy never received the publish"
        assert got[0] == {"state": "wake"}
        topic.unsubscribe()
    finally:
        client.terminate()
        stop.set()
        thread.join(5)
