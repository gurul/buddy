"""The OpenAI seam retries a TLS drop once (computer_agent.make_response_creator / make_stream_creator).

Live, 2026-09-28: three phone-call turns died on a bare ``ssl.SSLError`` the SDK does not retry, and the owner
heard FAILED_LINE. These tests fake the client so no network is touched.
"""

from __future__ import annotations

import asyncio
import ssl
from typing import Any

import pytest

from cc_buddy_bridge.computer_agent import make_response_creator, make_stream_creator


class Reply:
    def __init__(self, text: str) -> None:
        self.text = text

    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": self.text}]}]}


def tls_drop() -> ssl.SSLError:
    err = ssl.SSLError(1, "[SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC] bad record mac")
    err.reason = "DECRYPTION_FAILED_OR_BAD_RECORD_MAC"
    return err


class FakeResponses:
    """``create`` and ``stream`` play a script: an exception to raise, or a list of text deltas to emit."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls = 0

    async def create(self, **request: Any) -> Reply:
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return Reply(step)

    def stream(self, **request: Any) -> "FakeStream":
        self.calls += 1
        return FakeStream(self.script.pop(0))


class Delta:
    type = "response.output_text.delta"

    def __init__(self, delta: str) -> None:
        self.delta = delta


class FakeStream:
    """A step is a list: text deltas, and an exception raised where it appears in the list."""

    def __init__(self, step: Any) -> None:
        self.step = step if isinstance(step, list) else [step]

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __aiter__(self) -> "FakeStream":
        self._it = iter(self.step)
        return self

    async def __anext__(self) -> Delta:
        try:
            item = next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None
        if isinstance(item, BaseException):
            raise item
        return Delta(item)

    async def get_final_response(self) -> Reply:
        return Reply("".join(s for s in self.step if isinstance(s, str)))


class FakeClient:
    def __init__(self, *script: Any) -> None:
        self.responses = FakeResponses(*script)


def text_of(response: dict[str, Any]) -> str:
    return response["output"][0]["content"][0]["text"]


def test_a_tls_drop_is_retried_once() -> None:
    client = FakeClient(tls_drop(), "hello")
    create = make_response_creator(client=client)
    assert text_of(asyncio.run(create({}))) == "hello"
    assert client.responses.calls == 2


def test_two_tls_drops_in_a_row_are_an_outage() -> None:
    client = FakeClient(tls_drop(), tls_drop(), "never")
    create = make_response_creator(client=client)
    with pytest.raises(ssl.SSLError):
        asyncio.run(create({}))
    assert client.responses.calls == 2


def test_other_errors_are_not_retried() -> None:
    client = FakeClient(RuntimeError("boom"), "never")
    create = make_response_creator(client=client)
    with pytest.raises(RuntimeError):
        asyncio.run(create({}))
    assert client.responses.calls == 1


def test_a_stream_that_drops_before_any_text_is_retried() -> None:
    client = FakeClient([tls_drop()], ["It's ", "a desk."])
    create = make_stream_creator(client=client)
    heard: list[str] = []
    out = asyncio.run(create({}, heard.append))
    assert heard == ["It's ", "a desk."] and text_of(out) == "It's a desk."
    assert client.responses.calls == 2


def test_a_stream_that_drops_after_text_was_read_out_is_not_retried() -> None:
    # a retry would read "It's " out twice; the turn fails instead and the caller says so
    client = FakeClient(["It's ", tls_drop()], ["never"])
    create = make_stream_creator(client=client)
    heard: list[str] = []
    with pytest.raises(ssl.SSLError):
        asyncio.run(create({}, heard.append))
    assert heard == ["It's "]
    assert client.responses.calls == 1
