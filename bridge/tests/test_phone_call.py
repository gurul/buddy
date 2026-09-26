"""phone_call.py: "Call buddy" in the Mini App, push to talk into the chat's brain. The real Mini App server on a
loopback port, a real WebSocket client (the websockets library, as the phone's browser), a fake chat that answers
through the call's reader, and a fake voice (no OpenAI)."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from cc_buddy_bridge import miniapp, phone_call
from cc_buddy_bridge.miniapp import MiniAppConfig, MiniAppServer, SpendLedger, check_init_data, sign_init_data

TOKEN = "123456:TEST-token"
OWNER = 4242


def signed(user_id: int = OWNER) -> str:
    return sign_init_data({"auth_date": str(int(time.time())), "query_id": "AAH",
                           "user": json.dumps({"id": user_id, "first_name": "O"})}, TOKEN)


class Brain:
    """The chat: hears the owner's words and answers through the call's reader, as telegram.TelegramInlet does."""

    def __init__(self, reply: str = "You have **two** meetings: [standup](https://x.example) and lunch.") -> None:
        self.reply = reply
        self.heard: list[str] = []
        self.say: Any = None

    def listen(self, say) -> bool:
        self.say = say
        return True

    def hear(self, text: str) -> None:
        self.heard.append(text)
        if self.say is not None and self.reply:
            self.say(self.reply)


class Voice:
    def __init__(self, text: str = "what's on my calendar") -> None:
        self.text = text
        self.spoken: list[str] = []
        self.transcribed: list[int] = []

    def transcribe(self, pcm: bytes) -> str:
        self.transcribed.append(len(pcm))
        return self.text

    def speak_stream(self, text: str):
        self.spoken.append(text)
        yield b"\x01\x00" * 1200
        yield b"\x01"                                                   # an odd chunk: carried, never split
        yield b"\x00" + b"\x01\x00" * 1199

    def live_ears(self):
        return None


async def serve(tmp_path: Path, brain: Brain | None, voice: Voice | None = None, public: str = "", on: bool = True):
    cfg = MiniAppConfig(enabled=True, token=TOKEN, owner_ids=frozenset({OWNER}), ledger_path=tmp_path / "l.json")
    server = MiniAppServer(cfg, SpendLedger(cfg.ledger_path), page=b"home")
    if on:
        server.calls = phone_call.PhoneCalls(lambda d: check_init_data(d, TOKEN, cfg.owner_ids), brain,
                                             voice if voice is not None else Voice())
    port = await server.start()
    server.public_url = public
    return server, f"ws://127.0.0.1:{port}/api/call", f"http://127.0.0.1:{port}"


async def until(ws, pred) -> list[Any]:
    out: list[Any] = []
    async with asyncio.timeout(5):
        while True:
            m = await ws.recv()
            out.append(m if isinstance(m, bytes) else json.loads(m))
            if pred(out[-1]):
                return out


def is_(kind: str, **fields):
    return lambda m: isinstance(m, dict) and m.get("type") == kind and all(m.get(k) == v for k, v in fields.items())


async def press(ws, secs: float = 1.0) -> None:
    await ws.send(json.dumps({"type": "talk"}))
    for _ in range(int(secs * 10)):
        await ws.send(bytes(4800))
    await ws.send(json.dumps({"type": "done"}))


def test_the_accept_key_is_rfc_6455s():
    assert phone_call.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_a_press_is_heard_handed_to_the_chat_and_the_reply_read_back(tmp_path):
    brain, voice = Brain(), Voice()

    async def go():
        server, url, origin = await serve(tmp_path, brain, voice)
        async with connect(url, origin=origin) as ws:
            await ws.send(json.dumps({"initData": signed()}))
            await until(ws, is_("state", state="connected"))
            await ws.send(bytes(4800))                                   # before a press: never recorded
            await press(ws, 1.0)
            got = await until(ws, is_("state", state="listening"))
            await ws.send(json.dumps({"type": "end"}))
            ended = await until(ws, is_("ended"))
        await server.close()
        return got, ended

    got, ended = asyncio.run(go())
    assert {"type": "heard", "text": "what's on my calendar"} in got
    assert brain.heard == ["what's on my calendar"]
    assert voice.transcribed == [48000]                                  # the press alone: 1 s at 24 kHz
    assert voice.spoken == ["You have two meetings: standup and lunch."]  # Markdown and links read as words
    assert sum(len(m) for m in got if isinstance(m, bytes)) == 4800
    assert [m["state"] for m in got if isinstance(m, dict) and m.get("type") == "state"][:2] == ["thinking", "speaking"]
    assert ended[-1] == {"type": "ended", "reason": "you hung up"}
    assert brain.say is None                                             # the chat stops reading to a gone call


def test_a_tap_is_not_words_and_nothing_is_asked(tmp_path):
    brain = Brain()

    async def go():
        server, url, origin = await serve(tmp_path, brain)
        async with connect(url, origin=origin) as ws:
            await ws.send(json.dumps({"initData": signed()}))
            await press(ws, 0.1)
            note = await until(ws, lambda m: isinstance(m, dict) and bool(m.get("note")))
        await server.close()
        return note

    assert "Hold the button" in asyncio.run(go())[-1]["note"] and brain.heard == []


def test_a_press_while_buddy_talks_interrupts_it(tmp_path):
    class SlowVoice(Voice):
        def speak_stream(self, text: str):
            time.sleep(0.2)
            yield from super().speak_stream(text)

    brain = Brain(reply="One. " * 60)                                  # many sentences, slowly made

    async def go():
        server, url, origin = await serve(tmp_path, brain, SlowVoice())
        async with connect(url, origin=origin) as ws:
            await ws.send(json.dumps({"initData": signed()}))
            await press(ws)
            await until(ws, is_("state", state="speaking"))
            await ws.send(json.dumps({"type": "talk"}))
            got = await until(ws, is_("flush"))
        await server.close()
        return got

    assert asyncio.run(go())[-1] == {"type": "flush"}


@pytest.mark.parametrize("hello", [{"initData": ""}, {"initData": "garbage"}, "not json", {"x": 1}])
def test_nobody_but_the_owner_can_call(tmp_path, hello):
    brain = Brain()

    async def go():
        server, url, origin = await serve(tmp_path, brain)
        async with connect(url, origin=origin) as ws:
            await ws.send(hello if isinstance(hello, str) else json.dumps(hello))
            assert (await until(ws, is_("ended")))[-1]["reason"] == "Only buddy's owner can call."
        stranger = sign_init_data({"auth_date": str(int(time.time())), "user": json.dumps({"id": 999})}, TOKEN)
        async with connect(url, origin=origin) as ws:
            await ws.send(json.dumps({"initData": stranger}))
            assert (await until(ws, is_("ended")))[-1]["reason"] == "Only buddy's owner can call."
        await server.close()

    asyncio.run(go())
    assert brain.say is None                                            # nothing was started


def test_calling_again_replaces_a_call_left_open_and_calls_are_off_without_a_chat(tmp_path):
    async def go():
        server, url, origin = await serve(tmp_path, Brain())
        async with connect(url, origin=origin) as first:
            await first.send(json.dumps({"initData": signed()}))
            await until(first, is_("state", state="connected"))
            async with connect(url, origin=origin) as second:           # the page reopened; the old socket lingers
                await second.send(json.dumps({"initData": signed()}))
                assert (await until(second, is_("state", state="connected")))[-1]["state"] == "connected"
                assert server.calls.active is not None and server.calls.active.ws is not None
        await server.close()
        server, url, origin = await serve(tmp_path, None)
        async with connect(url, origin=origin) as ws:
            await ws.send(json.dumps({"initData": signed()}))
            assert "aren't set up" in (await until(ws, is_("ended")))[-1]["reason"]
        await server.close()

    asyncio.run(go())


def test_a_phone_that_goes_quiet_ends_the_call(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_call, "QUIET_SECS", 0.3)
    brain = Brain()

    async def go():
        server, url, origin = await serve(tmp_path, brain)
        async with connect(url, origin=origin) as ws:
            await ws.send(json.dumps({"initData": signed()}))
            assert (await until(ws, is_("ended")))[-1]["reason"] == "the phone went quiet"
        assert server.calls.active is None and brain.say is None
        await server.close()

    asyncio.run(go())


def test_the_upgrade_is_refused_from_elsewhere_or_when_calls_are_off(tmp_path):
    async def go():
        server, url, _ = await serve(tmp_path, Brain(), public="https://buddy.example.trycloudflare.com")
        for origin in ("https://evil.example", "null"):
            with pytest.raises(InvalidStatus) as e:
                async with connect(url, origin=origin):
                    pass
            assert e.value.response.status_code == 403
        await server.close()
        server, url, origin = await serve(tmp_path, None, on=False)
        with pytest.raises(InvalidStatus) as e:
            async with connect(url, origin=origin):
                pass
        assert e.value.response.status_code == 404
        await server.close()

    asyncio.run(go())


def test_a_silent_socket_is_closed_after_the_auth_window(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_call, "AUTH_SECS", 0.2)

    async def go():
        server, url, origin = await serve(tmp_path, Brain())
        async with connect(url, origin=origin) as ws:
            assert (await until(ws, is_("ended")))[-1]["reason"] == "Only buddy's owner can call."
        await server.close()

    asyncio.run(go())


def test_speakable_reads_a_chat_message_as_words():
    assert phone_call.speakable("**Done.** See [the doc](https://d.example/x) or https://y.example/z") == \
        "Done. See the doc or a link"
    assert phone_call.speakable("- one\n- two") == "one two"
    long = "A sentence here. " * 200
    out = phone_call.speakable(long)
    assert len(out) <= phone_call.MAX_SPOKEN_CHARS + 40 and out.endswith("The rest is in the chat.")
    assert phone_call.sentences("Hi. Yes. " + "This one is long enough to stand alone as a sentence of its own. " * 2) \
        [0].startswith("Hi. Yes.")


def test_frames_round_trip_and_bad_frames_close():
    async def go():
        reader = asyncio.StreamReader()
        payload = b"x" * 70000                                       # the 64-bit length form
        mask = b"\x01\x02\x03\x04"
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        reader.feed_data(bytes([0x82, 0x80 | 127]) + len(payload).to_bytes(8, "big") + mask + masked)
        ws = phone_call.WebSocket(reader, None)                      # type: ignore[arg-type]
        assert await ws.recv() == (phone_call.OP_BIN, payload)
        reader.feed_data(bytes([0x82, 0x05]) + b"hello")                # unmasked: a client must mask
        with pytest.raises(phone_call.Closed):
            await ws.recv()
        big = phone_call.MAX_FRAME_BYTES + 1
        reader2 = asyncio.StreamReader()
        reader2.feed_data(bytes([0x82, 0x80 | 127]) + big.to_bytes(8, "big"))
        with pytest.raises(phone_call.Closed):
            await phone_call.WebSocket(reader2, None).recv()     # type: ignore[arg-type]

    asyncio.run(go())
    assert phone_call.frame(phone_call.OP_TEXT, b"hi") == b"\x81\x02hi"


def test_the_page_has_the_call_button_and_speaks_the_wire_format():
    page = miniapp.PAGE_PATH.read_text()
    assert 'id="call-open"' in page and 'new URL("api/call"' in page
    assert "RATE = 24000" in page and "JSON.stringify({ initData })" in page and 'id="call-talk"' in page
    assert 'type: "talk"' in page and 'type: "done"' in page


# ---- latency: live ears, the fast voice and their fallbacks -------------------------------------------

class FakeEars:
    def __init__(self, text: str = "live words", fail: bool = False) -> None:
        self.text, self.fail = text, fail
        self.fed = 0
        self.begun = 0
        self.broken = False

    async def open(self) -> None:
        pass

    async def begin(self) -> None:
        self.begun += 1
        self.fed = 0

    async def feed(self, pcm: bytes) -> None:
        self.fed += len(pcm)

    async def finish(self, timeout: float) -> str:
        if self.fail:
            raise TimeoutError("no text")
        return self.text

    async def close(self) -> None:
        pass


class LiveVoice(Voice):
    def __init__(self, ears: FakeEars) -> None:
        super().__init__(text="uploaded words")
        self.ears = ears

    def live_ears(self):
        return self.ears


@pytest.mark.parametrize("fail,expect", [(False, "live words"), (True, "uploaded words")])
def test_a_press_is_transcribed_live_and_falls_back_to_the_upload(tmp_path, fail, expect):
    brain, ears = Brain(reply=""), FakeEars(fail=fail)
    voice = LiveVoice(ears)

    async def go():
        server, url, origin = await serve(tmp_path, brain, voice)
        async with connect(url, origin=origin) as ws:
            await ws.send(json.dumps({"initData": signed()}))
            await until(ws, is_("state", state="connected"))
            await asyncio.sleep(0.05)                                # the live session opens meanwhile
            await press(ws, 1.0)
            got = await until(ws, is_("heard"))
        await server.close()
        return got

    assert asyncio.run(go())[-1]["text"] == expect
    assert brain.heard == [expect] and ears.fed == 48000 and ears.begun == 1
    assert voice.transcribed == ([48000] if fail else [])           # uploaded only when the live one failed


def test_live_ears_join_every_piece_in_order_and_take_an_empty_commit():
    class Conn:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.input_audio_buffer = self

        async def commit(self) -> None:
            self.sent.append("commit")

    async def go():
        ears = phone_call.LiveEars("k", "m")
        ears.conn = Conn()
        # the server cut the press at a pause, and transcribes piece 2 before piece 1 finishes
        ears.order = ["i1"]
        finishing = asyncio.ensure_future(ears.finish(timeout=2))
        await asyncio.sleep(0)
        ears.order.append("i2")
        ears.commit_resolved = True
        ears.changed.set()
        await asyncio.sleep(0)
        ears.done["i2"] = "the dentist."
        ears.changed.set()
        await asyncio.sleep(0)
        assert not finishing.done()                                  # piece 1 still out
        ears.done["i1"] = "remind me to call"
        ears.changed.set()
        assert await finishing == "remind me to call the dentist."
        # the server had committed everything already: our commit is empty, and that is fine
        ears.order, ears.done = ["i3"], {"i3": "hi"}
        finishing = asyncio.ensure_future(ears.finish(timeout=2))
        await asyncio.sleep(0)
        ears.commit_resolved = True
        ears.changed.set()  # what the "commit_empty" error sets
        assert await finishing == "hi"
        ears.broken = True
        with pytest.raises(ConnectionError):
            await ears.finish(timeout=2)

    asyncio.run(go())


def test_the_fast_voice_falls_back_to_openai_before_a_sound_is_sent(monkeypatch):
    import contextlib as cl

    import httpx

    class Resp:
        def __init__(self, status: int) -> None:
            self.status_code = status

        def iter_bytes(self, n):
            yield b"\x01\x00" * 10

    calls: list[str] = []

    @cl.contextmanager
    def fake_stream(method, url, **kw):
        calls.append(kw["json"]["model"])
        yield Resp(503)

    class OpenAISpeech:
        @cl.contextmanager
        def create(self, **kw):
            calls.append(kw["model"])
            yield Resp(200)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    v = phone_call.OpenAIVoice.__new__(phone_call.OpenAIVoice)
    v.or_key, v.or_model, v.or_voice = "or-key", "deepgram/aura-2", "aura-2-thalia-en"
    v.tts_model, v.voice = "gpt-4o-mini-tts", "marin"
    v._client = type("C", (), {"audio": type("A", (), {"speech": type("S", (), {
        "with_streaming_response": OpenAISpeech()})()})()})()
    assert b"".join(v.speak_stream("hello")) == b"\x01\x00" * 10
    assert calls == ["deepgram/aura-2", "gpt-4o-mini-tts"]


def test_make_voice_picks_the_fast_voice_only_with_an_openrouter_key():
    v = phone_call.make_voice({"OPENAI_API_KEY": "sk-test", "OPENROUTER_API_KEY": "or-test"})
    assert v.or_key == "or-test" and v.or_model == "deepgram/aura-2" and v.live
    v = phone_call.make_voice({"OPENAI_API_KEY": "sk-test", "OPENROUTER_API_KEY": "or-test",
                               "CC_BUDDY_CALL_TTS_PROVIDER": "openai", "CC_BUDDY_CALL_LIVE_STT": "0"})
    assert v.or_key == "" and not v.live
    assert phone_call.make_voice({}) is None
