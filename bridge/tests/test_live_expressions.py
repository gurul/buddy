import asyncio
import threading
from contextlib import suppress

import pytest

from cc_buddy_bridge.live_expressions import LiveExpressions


class Model:
    def predict(self, text):
        return {"probabilities": [0, 1, 0, 0, 0, 0], "ms": 1.0}


async def until(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("worker did not reach expected state")


async def stop(task):
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    "muted,phase,chirp", [(False, "speaking", False), (True, "speaking", False), (False, "listening", False)]
)
def test_queue_and_arbitration(tmp_path, muted, phase, chirp):
    async def run():
        sent = []

        async def send(cmd):
            sent.append(cmd)
            return True

        service = LiveExpressions(
            send,
            path=tmp_path / "settings.json",
            model_factory=Model,
            muted=lambda: muted,
            phase=lambda: phase,
        )
        await service.set_enabled(True)
        service.offer("assistant", "An earlier obsolete sentence.")
        event = service.offer("assistant", "This is such wonderful news!")
        task = asyncio.create_task(service.run())
        try:
            await until(lambda: len(sent) == 1)
            assert sent[0]["id"] == event and sent[0]["label"] == "happy"
            assert sent[0]["chirp"] is chirp
            assert 500 <= sent[0]["ttl_ms"] <= 4000
            assert service.dropped == 1
            assert service.offer("assistant", "This is such wonderful news!") is None
            await service.set_enabled(False)
            assert sent[-1]["clear"] is True
            assert service.offer("user", "hello") is None
        finally:
            await stop(task)

    asyncio.run(run())


def test_disabled_during_inference_drops_result(tmp_path):
    async def run():
        started, release = threading.Event(), threading.Event()

        class Slow(Model):
            def predict(self, text):
                started.set()
                release.wait(2)
                return super().predict(text)

        sent = []

        async def send(cmd):
            sent.append(cmd)

        service = LiveExpressions(send, path=tmp_path / "settings.json", model_factory=Slow)
        await service.set_enabled(True)
        service.offer("user", "Wonderful news!")
        task = asyncio.create_task(service.run())
        try:
            await until(started.is_set)
            await service.set_enabled(False)
            release.set()
            await until(lambda: service.dropped == 1)
            assert len(sent) == 1 and sent[0]["clear"]
        finally:
            release.set()
            await stop(task)

    asyncio.run(run())


def test_stale_and_send_failure(tmp_path):
    async def run():
        now = [1.0]

        async def send(cmd):
            return False

        service = LiveExpressions(
            send, path=tmp_path / "settings.json", model_factory=Model, clock=lambda: now[0]
        )
        await service.set_enabled(True)
        service.offer("diary", "Something interesting here.")
        now[0] = 10
        task = asyncio.create_task(service.run())
        try:
            await until(lambda: service.dropped == 1)
            service.offer("user", "A new interesting thing!")
            await until(lambda: service.dropped == 2)
            assert service.sent == 0 and service.last is None and "send failed" in service.error
        finally:
            await stop(task)

    asyncio.run(run())


@pytest.mark.parametrize("values", [[], [1] * 6, [float("nan"), 0, 0, 0, 0, 0], [True, 0, 0, 0, 0, 0]])
def test_invalid_results(values):
    with pytest.raises(ValueError):
        LiveExpressions.label({"probabilities": values})


def test_text_cannot_trigger_startle():
    assert LiveExpressions.label({"probabilities": [0, 0, 0, 0, 0, 1]}) == ("calm", 1)


@pytest.mark.parametrize("muted", [False, True])
def test_laya_keeps_original_caption_chirps(tmp_path, muted):
    from types import SimpleNamespace

    from cc_buddy_bridge.daemon import Daemon

    async def run():
        sent = []

        async def send(cmd):
            sent.append(cmd)

        daemon = SimpleNamespace(
            ble=SimpleNamespace(connected=True, send=send),
            _sound=SimpleNamespace(muted=muted),
            _expressions=SimpleNamespace(enabled=True, ready=True),
        )
        Daemon._on_caption(daemon, {"cmd": "caption", "chirp": True, "lines": ["Hello!"]})
        await asyncio.sleep(0)
        assert sent[0]["chirp"] is (not muted)

    asyncio.run(run())
